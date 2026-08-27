"""
backend/main.py
----------------
FastAPI REST server — all endpoints for the React SPA and eval harness.
Streaming: POST /answer/stream returns Server-Sent Events (SSE).
"""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import AsyncGenerator, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .answerer import answer_question, retrieve, top_context, call_draft, call_verify
from .chunker import chunk_segments
from .config import ABSTAIN_THRESHOLD, CONTEXT_TOP_K, CORS_ORIGINS, FILINGS_DIR
from .footnote_graph import build_footnote_graph, enrich_context_with_footnotes
from .indexer import index_chunks, list_indexed_filings
from .intelligence import run_intelligence, load_intelligence
from .learning import record_feedback, store_query_pattern
from .models import AnswerResponse, EvidenceItem, QuestionRequest
from .parser import parse_filing
from .retriever import retrieve as do_retrieve, top_context

app = FastAPI(
    title="The Analyst Copilot API",
    description="Grounded question answering over SEC 10-K/10-Q/8-K filings.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_NOT_FOUND = "not found in this filing"

# ---------------------------------------------------------------------------
# Core endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {
        "service": "analyst-copilot-api",
        "version": "2.0.0",
        "status": "ok",
        "indexed_filings": len(list_indexed_filings()),
    }


@app.get("/health")
def health():
    filings = list_indexed_filings()
    return {"status": "ready" if filings else "empty", "indexed_count": len(filings)}


@app.get("/filings")
def get_filings() -> List[str]:
    return list_indexed_filings()


@app.post("/upload")
async def upload_filings(files: List[UploadFile] = File(...)):
    """
    Accepts .htm/.html SEC filings.  Immediately parses/chunks/indexes
    them (Track B — 10-min SLA gate).  Footnote graph + Intelligence
    Layer run in a background thread (async, non-blocking).
    """
    results = []
    for upload in files:
        if not upload.filename.lower().endswith((".htm", ".html", ".txt")):
            results.append({"filename": upload.filename, "status": "skipped (unsupported type)"})
            continue

        dest = FILINGS_DIR / upload.filename
        with dest.open("wb") as f:
            shutil.copyfileobj(upload.file, f)

        doc_name = dest.stem
        html = dest.read_text(encoding="utf-8", errors="ignore")
        parsed = parse_filing(html, doc_name=doc_name)
        chunks = chunk_segments(parsed.segments, doc_name=doc_name)
        index_chunks(chunks, doc_name=doc_name)

        # Non-blocking enrichment (does not gate SLA)
        def _enrich(p=parsed):
            try:
                build_footnote_graph(p)
                run_intelligence(p)
            except Exception:
                pass

        threading.Thread(target=_enrich, daemon=True).start()

        results.append({
            "filename": upload.filename,
            "doc_name": doc_name,
            "status": "indexed",
            "segments": len(parsed.segments),
            "chunks": len(chunks),
            "page_num_method": parsed.page_num_method.value,
        })

    return {"results": results}


@app.post("/answer", response_model=AnswerResponse)
@app.post("/api/answer", response_model=AnswerResponse)
async def answer(request: QuestionRequest) -> AnswerResponse:
    if not list_indexed_filings():
        raise HTTPException(
            status_code=400,
            detail="No filings indexed yet. Upload or ingest filings first.",
        )
    resp = await answer_question(request.question, doc_name=request.doc_name, top_k=request.top_k)
    store_query_pattern(
        request.question,
        request.doc_name,
        "abstained" if resp.abstained else "answered",
    )
    return resp


# ---------------------------------------------------------------------------
# SSE Streaming endpoint (§9)
# ---------------------------------------------------------------------------

async def _stream_answer(question: str, doc_name: str, top_k: int) -> AsyncGenerator[str, None]:
    """
    Yields Server-Sent Events:
      event: retrieval  — retrieved chunks summary
      event: draft      — draft answer (Pass 1)
      event: verify     — verification result (Pass 2)
      event: answer     — final gated answer
      event: error      — on failure
    """
    def sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    if not list_indexed_filings():
        yield sse("error", {"message": "No filings indexed yet."})
        return

    # Step 1: retrieval
    from .retriever import retrieve as do_retrieve, top_context
    from .footnote_graph import enrich_context_with_footnotes
    from .answerer import call_draft, call_verify, _format_context, _resolve_doc_name

    resolved_doc = _resolve_doc_name(doc_name)
    retrieval = await do_retrieve(question, doc_name=resolved_doc, top_k=top_k)
    context_chunks = top_context(retrieval, n=CONTEXT_TOP_K)

    yield sse("retrieval", {
        "chunks": len(context_chunks),
        "agreement_ratio": round(retrieval.agreement_ratio, 3),
        "expanded_query": retrieval.expanded_query,
    })

    if not context_chunks:
        yield sse("answer", {
            "answer": _NOT_FOUND,
            "abstained": True,
            "confidence": 0.0,
            "page_num": None,
            "doc_name": resolved_doc,
            "evidence": [],
        })
        return

    # Step 2: build context + footnote enrichment
    footnote_addon = enrich_context_with_footnotes(context_chunks)
    context_text = _format_context(context_chunks, footnote_addon)
    evidence = [
        {"text": rc.chunk.text, "page_num": rc.chunk.page_num,
         "doc_name": rc.chunk.doc_name, "chunk_type": rc.chunk.chunk_type.value}
        for rc in context_chunks
    ]

    # Step 3: draft (Pass 1)
    try:
        draft = call_draft(question, context_text)
        yield sse("draft", {
            "answer": draft.answer,
            "value": draft.value,
            "page_num": draft.page_num,
            "confidence": draft.confidence,
        })
    except Exception as exc:
        yield sse("error", {"message": f"LLM unavailable: {exc}"})
        yield sse("answer", {
            "answer": "LLM unavailable — showing raw evidence only, unverified.",
            "abstained": True,
            "confidence": 0.0,
            "doc_name": resolved_doc,
            "evidence": evidence,
        })
        return

    # Step 4: verify (Pass 2)
    try:
        verify = call_verify(question, draft, context_text)
        yield sse("verify", {
            "verified": verify.verified,
            "confidence": verify.confidence,
            "issues": verify.issues,
        })
    except Exception as exc:
        yield sse("error", {"message": f"Verify LLM unavailable: {exc}"})
        verify = None

    # Step 5: precision gate
    if verify:
        final_conf = min(draft.confidence, verify.confidence)
        abstained = (not verify.verified) or final_conf < ABSTAIN_THRESHOLD
        final_answer = (
            _NOT_FOUND if abstained
            else (verify.correct_value or draft.value or draft.answer)
        )
        final_page = None if abstained else (verify.correct_page or draft.page_num)
    else:
        abstained = True
        final_conf = 0.0
        final_answer = _NOT_FOUND
        final_page = None

    yield sse("answer", {
        "answer": final_answer,
        "abstained": abstained,
        "confidence": final_conf,
        "retrieval_agreement": retrieval.agreement_ratio,
        "page_num": final_page,
        "doc_name": resolved_doc,
        "evidence": evidence,
    })


@app.post("/answer/stream")
async def answer_stream(request: QuestionRequest):
    """
    Streaming SSE endpoint.  Client listens for events:
      retrieval, draft, verify, answer, error.
    """
    return StreamingResponse(
        _stream_answer(request.question, request.doc_name, request.top_k),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Intelligence Layer endpoint
# ---------------------------------------------------------------------------

@app.get("/filings/{doc_name}/intelligence")
def filing_intelligence(doc_name: str):
    return load_intelligence(doc_name)


# ---------------------------------------------------------------------------
# Feedback endpoint (learning loops)
# ---------------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    answer_id: Optional[str] = None
    chunk_ids: List[str] = []
    verdict: str
    note: Optional[str] = None


@app.post("/feedback")
def feedback(req: FeedbackRequest):
    for chunk_id in req.chunk_ids:
        record_feedback(chunk_id, req.verdict)
    return {"status": "recorded", "verdict": req.verdict, "chunks": len(req.chunk_ids)}


if __name__ == "__main__":
    import uvicorn
    from .config import API_HOST, API_PORT
    uvicorn.run("backend.main:app", host=API_HOST, port=API_PORT, reload=True)
