# Approach Note — The Analyst Copilot

## The core problem we solved

Generic RAG over SEC filings fails in three specific, measurable ways:
HTML table rows get split across chunks (structural grid fragmentation),
parenthesized negatives and scale headers get stripped, and electronic
page numbers diverge from printed booklet page numbers by ~10 pages.
We fixed all three deterministically before any LLM is involved.

## What we built

A two-track ingestion system on top of a validated parsing core, with
a two-pass QA engine and calibrated abstention.

**Parser:** BeautifulSoup4 with three tested fixes against real filings
(3M 2018 10-K, JPMorgan 2022 10-K). Colspan-interval expansion ensures
a table value is always matched to the header that logically covers it.
A snapshot-before-iterate pattern prevents BS4 live-generator truncation.
A defensive attrs helper prevents `AttributeError` on malformed markup.

**Chunker:** 400-word prose windows with 50-word overlap. Table-row fact
sentences and footnote definitions are preserved as atomic single-fact
chunks — never merged, never split.

**Retrieval:** Hybrid RRF (ChromaDB dense + BM25 sparse). GAAP synonym
expansion is fed to BM25 only, keeping the dense embedding semantically
clean. A 1.5× dual-agreement multiplier promotes chunks that both engines
agree on. A query classifier routes simple numeric lookups to a SQLite
fast-path cache (<100ms, no RAG) and cross-company questions to a per-filing
merge pass.

**QA:** Two independent LLM passes. Pass 1 (Draft) extracts a candidate
answer, value, page citation, and confidence. Pass 2 (Verify) audits the
draft against the same raw excerpts — is the value literally present? does
the period match? is the page citation correct? The gate: output the answer
only if verified == True AND min(draft.conf, verify.conf) ≥ 0.75. Below
that threshold, output "not found in this filing." Under the FinanceBench
rubric, a wrong answer scores −1 while an abstention scores 0. Guessing
is strictly dominated.

**LLM provider — Azure OpenAI GPT-4o:** The primary provider is Azure
OpenAI (GPT-4o deployment) accessed via the `openai` SDK's `AzureOpenAI`
client. The two-pass Draft/Verify architecture is provider-agnostic — the
`_dispatch()` layer supports `azure_openai`, `openai`, `gemini`, and
`ollama` via a single function interface. Azure OpenAI was chosen over
other hosted providers for enterprise data residency, rate-limit control,
and direct integration with the rest of the Azure deployment stack (App
Service, Key Vault, Container Apps). If the primary provider fails, the
system retries against a local Ollama daemon (llama3.1) before surfacing
an error — no single point of API dependency at demo time.

## PRISM differentiator layer (async, non-blocking)

After a filing is queryable, a background thread runs:
- Footnote graph (NetworkX): auto-appends resolved footnote text to
  retrieved chunks at query time.
- Intelligence Layer: 12 red-flag checks, MD&A tone scoring (0–100),
  cross-section contradiction detection, per-company anomaly fingerprinting,
  and a weighted conviction score (not investment advice).
- Learning loops: chunk weight feedback (+0.1 correct / −0.15 wrong),
  query pattern library, and rephrase-event synonym extraction.

## Evaluation discipline

136 practice questions across 78 filings (FinanceBench, Patronus AI).

**Filing-level holdout selection (§8.2):** 10–12 filings are held back and
never ingested until thresholds are frozen. Selection is question-count-aware:
filings are weighted by how many practice questions reference them, so every
reserved slot carries maximum evaluation signal. Pure random selection risks
reserving filings with zero linked questions — wasted holdout slots. The
selection is seeded (seed=42) for full reproducibility.

**Question split (§8.1):** Questions tied to the ~66–68 processed filings
are stratified 75/25 by (company, question_type) into a tune set and a small
validation slice. The `question_type` classifier (`simple` / `narrative` /
`footnote`) is a keyword heuristic — a short manual spot-check (§8.3) after
the automated split catches obvious misclassifications before thresholds are
locked.

**Three-phase evaluation protocol:**

1. **Tune phase** — `run_eval --mode tune`: run the tune set in filing scope
   against the ~66–68 processed filings. Used only for threshold search
   (ABSTAIN_THRESHOLD, RRF_K, DUAL_AGREEMENT_MULTIPLIER). The held-out set
   is not touched at any point during tuning.

2. **Before-ingest check** — `run_eval --mode before`: run the held-out
   filing questions in **global scope** (`doc_name=ALL`) before those filings
   are ingested. This is the real failure-mode test — the retriever is free
   to pull a plausible-looking chunk from a different, already-indexed filing
   and the Draft/Verify pipeline must correctly recognize it does not answer
   the question. A confident wrong answer here scores −1. If the system
   produces many −1s in this pass, it means the Verify gate is letting
   misattributed evidence through and the abstention threshold needs raising.
   Running the check in document scope (`doc_name=<reserved filing>`) would
   trivially abstain on every question — that index entry doesn't exist yet —
   and test nothing.

3. **After-ingest check** — `run_eval --mode after`: after the reserved
   filings are uploaded via the Track B live-ingest path, run the same
   question set in filing scope. This is the definitive accuracy score on a
   genuinely unseen filing — the only evaluation that matches the brief's
   framing of "a filing it has never seen." Using the same questions for both
   (2) and (3) maximises signal from a small sample (~18–22 questions across
   10–12 filings) rather than splitting it into two disjoint groups of ~9–11.

## What we did not do

We did not use GPT-4o's vision capability for table extraction — the
colspan/spacer-cell fix makes image-based table parsing unnecessary, and
deterministic HTML parsing is faster, cheaper, and fully auditable. GPT-4o
is used purely for its text reasoning in the Draft and Verify passes.
We did not build custom embeddings — all-MiniLM-L6-v2 is 90MB, locally
cached, and sufficient for financial domain retrieval at this scale.
We did not invoke the LLM during ingestion — the 10-minute Track B SLA
is met by deterministic parsing alone.
