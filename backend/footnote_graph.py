"""
backend/footnote_graph.py
--------------------------
NetworkX directed footnote graph: builds a per-filing graph of footnote
callouts → footnote definition text, serializes it to JSON, and resolves
callout references found in retrieved chunks at query time.

The graph is populated asynchronously after a filing is indexed
(non-blocking; does not gate the 10-minute SLA).

Node:  footnote_id  (str, e.g. "1", "12")
Edge:  callout_page -> footnote_id  (with text + page metadata)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

try:
    import networkx as nx
    _NX_AVAILABLE = True
except ImportError:
    _NX_AVAILABLE = False

from .config import DATA_DIR
from .models import ParsedFiling

GRAPH_DIR = DATA_DIR / "footnote_graphs"
GRAPH_DIR.mkdir(parents=True, exist_ok=True)

_CALLOUT_RE = re.compile(r"\((\d{1,2})\)")
_NOTE_RE = re.compile(r"\bNote\s+(\d{1,2})\b", re.IGNORECASE)


def _graph_path(doc_name: str) -> Path:
    return GRAPH_DIR / f"{doc_name}_footnotes.json"


def build_footnote_graph(parsed: ParsedFiling) -> Dict[str, dict]:
    """
    Builds a dict of {footnote_id: {text, page_num, doc_name}} from a
    ParsedFiling.  Footnote segments are segments whose footnote_id is set;
    those carry the definition text.  Returns the dict and persists it to
    JSON so later processes can load it without re-parsing.
    """
    graph: Dict[str, dict] = {}
    for seg in parsed.segments:
        if seg.footnote_id:
            graph[seg.footnote_id] = {
                "text": seg.text,
                "page_num": seg.page_num,
                "doc_name": parsed.doc_name,
            }

    # Persist
    path = _graph_path(parsed.doc_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2)

    return graph


def load_footnote_graph(doc_name: str) -> Dict[str, dict]:
    path = _graph_path(doc_name)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_callouts(text: str, doc_name: str) -> List[str]:
    """
    Scans text for footnote callouts like '(1)', 'Note 3', and returns
    a list of resolved footnote definition strings for the filing.
    Returns empty list if no graph exists or no callouts found.
    """
    graph = load_footnote_graph(doc_name)
    if not graph:
        return []

    found_ids = set()
    for m in _CALLOUT_RE.finditer(text):
        found_ids.add(m.group(1))
    for m in _NOTE_RE.finditer(text):
        found_ids.add(m.group(1))

    results = []
    for fid in sorted(found_ids):
        if fid in graph:
            entry = graph[fid]
            results.append(
                f"[Footnote {fid}, p.{entry['page_num']}] {entry['text']}"
            )
    return results


def enrich_context_with_footnotes(
    context_chunks: list, doc_names: Optional[List[str]] = None
) -> str:
    """
    Given a list of RetrievedChunk objects, appends resolved footnote
    definitions for any callouts found in those chunks.
    Returns additional footnote context as a string (may be empty).
    """
    footnote_texts = []
    seen = set()
    for rc in context_chunks:
        dn = rc.chunk.doc_name
        resolved = resolve_callouts(rc.chunk.text, dn)
        for r in resolved:
            if r not in seen:
                seen.add(r)
                footnote_texts.append(r)
    return "\n".join(footnote_texts)
