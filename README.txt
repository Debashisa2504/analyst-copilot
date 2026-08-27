THE ANALYST COPILOT — data pack
================================

Everything in this zip is PRACTICE material. It is yours to develop and test
against for the full two weeks.

WHAT IS IN HERE
---------------

practice-questions.jsonl
    136 analyst questions about the filings in this pack. Every question comes
    WITH its correct answer and the exact passage that proves it. Use these to
    build your system and to measure yourself honestly.

    One line = one question. The fields you need:
        question   - the analyst question, as asked
        answer     - the correct answer
        evidence   - the passage that proves it, and its page number
        doc_name   - which filing it refers to (matches a file in filings/)
        company, doc_period, doc_type - which company, which year, which form

filings/
    The annual and quarterly reports the questions are about, downloaded from
    the SEC's public archive. One file per document, named to match doc_name.
    These are the real thing: 70,000-150,000 words each, over 100 tables each.

    Source: https://www.sec.gov/edgar

DATA SOURCES AND LICENSES
-------------------------
Practice questions: FinanceBench, by Patronus AI
    Islam et al., "FinanceBench: A New Benchmark for Financial Question
    Answering" (arXiv:2311.11944). Licensed CC BY-NC 4.0
    (creativecommons.org/licenses/by-nc/4.0) - non-commercial use only.
    Used here, unmodified in content, for internal non-commercial purposes.

Filings: U.S. Securities and Exchange Commission, EDGAR
    (sec.gov/edgar) - public disclosure documents, freely accessible
    to anyone at no cost.
