"""
scripts/debug_retrieval.py
---------------------------
Diagnostic: shows exactly what retrieval returns for a question,
so you can tell BEFORE running the full eval whether the retriever
is finding anything useful.

Usage:
    python -m scripts.debug_retrieval \
        --doc BOEING_2018_10K \
        --question "What is Boeing's year end FY2018 net property, plant, and equipment?"

Output:
    - Query classification
    - BM25 top hits (local, no LLM)
    - Dense top hits (requires Azure embedding call)
    - Fused RRF result

This isolates whether the problem is retrieval (no chunks) vs LLM quality (bad answer from good chunks).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.indexer import query_bm25, query_dense, list_indexed_filings
from backend.learning.query_patterns import classify_query
from backend.retriever import expand_query, retrieve, top_context


async def main():
    parser = argparse.ArgumentParser(description="Debug retrieval for a single question.")
    parser.add_argument("--doc", required=True, help="doc_name (e.g. BOEING_2018_10K)")
    parser.add_argument("--question", required=True, help="Question text")
    parser.add_argument("--top-k", type=int, default=5, help="Chunks to show (default 5)")
    args = parser.parse_args()

    q = args.question
    doc = args.doc
    k = args.top_k

    print(f"\n{'='*70}")
    print(f"Question : {q}")
    print(f"Doc      : {doc}")
    print(f"{'='*70}\n")

    # --- 1. Classification ---
    q_type = classify_query(q)
    expanded = expand_query(q)
    print(f"[1] Query type  : {q_type}")
    print(f"    Expanded BM25: {expanded[:120]}{'...' if len(expanded) > 120 else ''}")

    # --- 2. Check if filing is indexed ---
    indexed = list_indexed_filings()
    if doc in indexed:
        print(f"\n[2] BM25 index  : FOUND ({doc}.pkl)")
    else:
        print(f"\n[2] BM25 index  : NOT FOUND — run ingest for this filing first")
        print(f"    Indexed     : {indexed[:5]}{'...' if len(indexed) > 5 else ''}")

    # --- 3. BM25 hits (local, no API) ---
    print(f"\n[3] BM25 top-{k} hits:")
    bm25_hits = query_bm25(expanded, doc, top_k=k)
    if not bm25_hits:
        print("    *** EMPTY — BM25 found nothing for this query ***")
        print("    Possible causes: wrong doc_name, filing not indexed, or query tokens not in any chunk")
    for i, h in enumerate(bm25_hits, 1):
        score = h.get("score", 0)
        page = h["metadata"]["page_num"]
        preview = h["text"][:120].replace("\n", " ")
        print(f"    [{i}] page={page} score={score:.3f} | {preview}...")

    # --- 4. Dense hits (Azure embedding call) ---
    print(f"\n[4] Dense top-{k} hits:")
    try:
        dense_hits = await query_dense(q, doc, top_k=k)
        if not dense_hits:
            print("    *** EMPTY — pgvector found nothing for this doc_name ***")
            print("    Possible causes: embedding not stored in DB, or doc_name mismatch in DB")
        for i, h in enumerate(dense_hits, 1):
            dist = h.get("distance", 0)
            page = h["metadata"]["page_num"]
            preview = h["text"][:120].replace("\n", " ")
            print(f"    [{i}] page={page} dist={dist:.4f} | {preview}...")
    except Exception as e:
        print(f"    ERROR: {e}")

    # --- 5. Full hybrid retrieval (as the answerer sees it) ---
    print(f"\n[5] Hybrid RRF top-{k} (what LLM receives):")
    try:
        result = await retrieve(q, doc_name=doc, top_k=k)
        chunks = top_context(result, n=k)
        print(f"    agreement_ratio={result.agreement_ratio:.2f}  (both BM25+dense agreed on this fraction)")
        if not chunks:
            print("    *** EMPTY — LLM receives NO context and will abstain immediately ***")
        for i, rc in enumerate(chunks, 1):
            c = rc.chunk
            print(f"    [{i}] page={c.page_num} score={rc.score:.4f} dual={rc.dual_agreement} | {c.text[:100].replace(chr(10),' ')}...")
    except Exception as e:
        print(f"    ERROR: {e}")

    print(f"\n{'='*70}")
    print("DIAGNOSIS:")
    if not bm25_hits and not dense_hits:
        print("  → Both BM25 and dense returned EMPTY. The filing may not be indexed,")
        print("    or there is a doc_name mismatch between the eval question and the DB.")
    elif not bm25_hits:
        print("  → BM25 returned empty but dense found chunks.")
        print("    Query terms don't appear in chunks. Expand synonyms or check chunking.")
    elif not dense_hits:
        print("  → Dense returned empty but BM25 found chunks.")
        print("    Embeddings may not be stored in PostgreSQL for this filing.")
        print("    Re-run ingest for this doc_name.")
    else:
        print("  → Both found chunks. If the LLM still abstains, the issue is in the")
        print("    draft/verify pass (confidence too low, verify rejected the answer).")
        print("    Try lowering ABSTAIN_THRESHOLD or check the LLM response quality.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
