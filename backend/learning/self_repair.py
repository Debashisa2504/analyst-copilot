"""
backend/learning/self_repair.py
----------------------------------
Failure self-repair loop.

When a user rephrases a question after an abstention, this module:
  1. Detects the rephrase event (same session, same doc_name, abstained then answered).
  2. Extracts a synonym pair (original terms → rephrased terms).
  3. Stores the pair for future auto-expansion of matching queries.

At retrieval time, get_repair_expansions() is called to augment
the BM25 query with previously successful rephrasings.
"""
from __future__ import annotations

import re
from typing import List, Optional

from . import _conn, record_rephrase
from ..cache import store_synonym_pair, get_learned_expansions


def detect_and_store_rephrase(
    original_question: str,
    rephrased_question: str,
    original_abstained: bool,
    rephrased_abstained: bool,
) -> bool:
    """
    If the original abstained and the rephrased succeeded, extract and
    store unique tokens from the rephrasing as synonym expansions.
    Returns True if a synonym pair was stored.
    """
    if not original_abstained or rephrased_abstained:
        return False

    orig_tokens = set(re.findall(r"\b\w{4,}\b", original_question.lower()))
    new_tokens = set(re.findall(r"\b\w{4,}\b", rephrased_question.lower()))
    novel = new_tokens - orig_tokens

    if not novel:
        return False

    # Store the original question -> novel terms as a synonym expansion
    expansion = " ".join(sorted(novel))
    record_rephrase(original_question, rephrased_question)
    store_synonym_pair(original_question, expansion)
    return True


def get_repair_expansions(query: str) -> List[str]:
    """
    Returns any learned synonym expansions for this query from past
    rephrase events. Injected into the BM25 expanded query.
    """
    return get_learned_expansions(query)
