# Demo Script

Estimated runtime: 8 minutes. One presenter, one browser tab.

---

## 0. Pre-demo checklist (do this 30 min before)

```bash
# Start the system
docker compose up  # or: python -m backend.main & cd frontend && npm run dev

# Verify health
curl http://localhost:8000/health
# Expected: {"status":"ready","indexed_count":N}  (N > 0 if corpus pre-loaded)

# Pre-warm embedding model (avoids cold-start lag on first query)
curl -X POST http://localhost:8000/answer \
  -H "Content-Type: application/json" \
  -d '{"question":"revenue","doc_name":"ALL","top_k":1}'
```

Keep a terminal open for live log streaming:
```bash
# Docker:
docker compose logs -f backend

# Manual:
# uvicorn logs stream to terminal automatically
```

---

## 1. Opening (30 sec)

> "SEC 10-K filings are 100–200 pages of dense financial and legal text.
> Analysts spend hours hunting for a single number. The Analyst Copilot
> answers grounded questions in seconds — and tells you exactly which page
> it came from. When it doesn't know, it says so honestly."

Point to the UI in the browser.

---

## 2. Upload a live filing (2 min — this is the timed SLA demo)

> "The brief asks for a filing it's never seen, indexed in under 10 minutes.
> Let's do that now."

1. Drag a fresh `.htm` filing onto the upload zone in the sidebar.
2. Watch the status update: "Parsing & indexing…"
3. Filing appears in the indexed list within ~10 seconds.

> "Parse, chunk, embed, index — done. The intelligence layer is running
> in the background; it never blocks you."

---

## 3. Ask a precise numeric question (2 min)

Select the newly uploaded filing from the scope dropdown.

Type: **"What was capital expenditure in FY2018?"**

Watch the streaming progress card:
- ✓ Retrieved N excerpts · agreement X%
- ✓ Draft: $1,577 million (conf 91%)
- ✓ Verified (conf 89%)

Result appears with:
- The answer: **$1,577 million**
- Citation badge: **SEC EDGAR Page 59**
- Confidence and retrieval agreement bars
- Expandable grounding evidence (the exact table row that sourced the answer)

> "It found the capex line from the cash flow statement, converted the
> parenthesized negative to a positive magnitude the way an analyst would
> say it, and cited the exact page. Click the evidence to see the raw fact."

---

## 4. Demonstrate honest abstention (1 min)

Type: **"What did the CEO say about quantum computing strategy?"**

Expected response: *"Not found in this filing — evidence was insufficient."*

> "It doesn't guess. Under the scoring rubric, a wrong answer costs −1.
> An abstention costs 0. The system is calibrated to only answer when it
> can verify the value is literally present in the text."

---

## 5. Cross-filing question (1 min)

Switch scope to **"All indexed filings"**.

Type: **"Which company had the highest net income in 2018?"**

> "Now it retrieves across every indexed filing simultaneously, merges the
> results, and answers from the best evidence."

---

## 6. Filing Intelligence badge (1 min)

Select a single filing from the sidebar. Expand the **Filing Intelligence** panel.

Point to:
- Tone score (MD&A hedging vs. confidence)
- Conviction score (data-quality signal, not investment advice)
- Red flag count (click to expand individual flags)

> "This runs asynchronously after upload — never slows down your first query.
> It's detecting things like AR growing faster than revenue, OCF/net income
> sign mismatches, and auditor changes."

---

## 7. Feedback loop (30 sec)

On the last answer, click 👍 or 👎.

> "Every piece of feedback adjusts the chunk weights in our retrieval memory.
> Correct answers get a +0.1 boost, wrong ones a −0.15 penalty. The system
> gets better with every session."

---

## 8. Score dashboard (30 sec)

Point to the session score in the top-right header.

> "Live FinanceBench-style scoring: +1 for a correct answer with a valid
> page citation, 0 for an honest abstention, −1 for a confidently wrong answer.
> We've run [N] questions this session: net score [+N]."

---

## Fallback talking points (if anything goes wrong)

**Upload takes longer than expected:**
> "The JPMorgan 2022 10-K is 16MB — our largest test filing. It parsed and
> indexed in about 10 seconds. Embedding adds ~30 seconds per 10K chunks.
> Still well inside the 10-minute SLA."

**LLM call fails:**
> "The fallback path returns the raw retrieved evidence directly, clearly
> labeled as unverified. The system never hard-fails."

**Wrong answer returned:**
> "That's actually useful data. When I click 👎, the retrieval memory
> down-weights the chunk that sourced that answer. The self-repair loop
> also captures any rephrase I make next."
