import { useCallback, useEffect, useState } from "react";
import Sidebar from "./components/Sidebar.jsx";
import QuestionForm from "./components/QuestionForm.jsx";
import AnswerCard from "./components/AnswerCard.jsx";
import ScoreDashboard from "./components/ScoreDashboard.jsx";
import { api, askQuestionStream } from "./api.js";

// Progressive streaming state shape
const EMPTY_STREAM = {
  phase: null,        // null | 'retrieval' | 'draft' | 'verify' | 'answer'
  retrieval: null,
  draft: null,
  verify: null,
  result: null,
  error: null,
};

export default function App() {
  const [filings, setFilings] = useState([]);
  const [scope, setScope] = useState("ALL");
  const [health, setHealth] = useState(null);
  const [history, setHistory] = useState([]);   // [{question, result, verdict}]
  const [streaming, setStreaming] = useState(EMPTY_STREAM);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [f, h] = await Promise.all([api.listFilings(), api.health()]);
      setFilings(f);
      setHealth(h);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function handleAsk(question) {
    setAsking(true);
    setError(null);
    setStreaming({ ...EMPTY_STREAM, phase: "retrieval" });

    try {
      let finalResult = null;

      await askQuestionStream(question, scope, (eventType, data) => {
        setStreaming((prev) => {
          switch (eventType) {
            case "retrieval":
              return { ...prev, phase: "retrieval", retrieval: data };
            case "draft":
              return { ...prev, phase: "draft", draft: data };
            case "verify":
              return { ...prev, phase: "verify", verify: data };
            case "answer":
              finalResult = data;
              return { ...prev, phase: "answer", result: data };
            case "error":
              return { ...prev, error: data.message };
            default:
              return prev;
          }
        });
      });

      if (finalResult) {
        setHistory((h) => [{ question, result: finalResult, verdict: null }, ...h]);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setAsking(false);
      // Keep streaming state visible for 200ms then clear progress indicators
      setTimeout(() => setStreaming(EMPTY_STREAM), 200);
    }
  }

  function handleVerdict(index, verdict) {
    setHistory((h) =>
      h.map((item, i) => (i === index ? { ...item, verdict } : item))
    );
  }

  const phaseLabel = {
    retrieval: "Retrieving evidence…",
    draft: "Drafting answer (Pass 1)…",
    verify: "Auditing answer (Pass 2)…",
    answer: "Finalising…",
  };

  return (
    <div className="flex h-screen overflow-hidden bg-ink-950 text-ink-100">
      <Sidebar
        filings={filings}
        scope={scope}
        onScopeChange={setScope}
        onFilingsChanged={refresh}
        health={health}
      />

      <main className="flex flex-1 flex-col overflow-hidden">
        <header className="flex items-center justify-between border-b border-ink-700 bg-ink-900/60 px-8 py-4">
          <div>
            <h2 className="text-sm font-medium text-ink-100">Question Answering</h2>
            <p className="font-mono text-[11px] text-ink-400">
              Scope:&nbsp;
              <span className="text-accent">
                {scope === "ALL" ? "All indexed filings" : scope}
              </span>
            </p>
          </div>
          <ScoreDashboard history={history} />
        </header>

        <div className="flex-1 overflow-y-auto px-8 py-6">
          {history.length === 0 && !asking && (
            <div className="mx-auto mt-16 max-w-md text-center">
              <p className="text-sm text-ink-400">
                Upload a filing on the left, choose a scope, and ask a grounded
                question. Answers cite the exact page and abstain honestly when
                evidence is missing.
              </p>
            </div>
          )}

          <div className="mx-auto flex max-w-3xl flex-col gap-6 pb-6">
            {/* Progressive streaming card */}
            {asking && (
              <div className="rounded-lg border border-ink-700 bg-ink-900 p-5">
                <p className="font-mono text-xs text-ink-400 animate-pulse mb-3">
                  {phaseLabel[streaming.phase] || "Starting…"}
                </p>

                {streaming.retrieval && (
                  <div className="font-mono text-[11px] text-ink-400 mb-1">
                    ✓ Retrieved {streaming.retrieval.chunks} excerpts
                    {" · "}agreement {Math.round((streaming.retrieval.agreement_ratio || 0) * 100)}%
                  </div>
                )}
                {streaming.draft && (
                  <div className="font-mono text-[11px] text-ink-300 mb-1">
                    ✓ Draft: <span className="text-ink-100">{streaming.draft.answer}</span>
                    {" "}(conf {Math.round((streaming.draft.confidence || 0) * 100)}%)
                  </div>
                )}
                {streaming.verify && (
                  <div className={`font-mono text-[11px] mb-1 ${streaming.verify.verified ? "text-good" : "text-warn"}`}>
                    {streaming.verify.verified ? "✓ Verified" : "⚠ Verification issues"}
                    {" "}(conf {Math.round((streaming.verify.confidence || 0) * 100)}%)
                  </div>
                )}
                {streaming.error && (
                  <p className="font-mono text-[11px] text-bad">{streaming.error}</p>
                )}
              </div>
            )}

            {history.map((item, i) => (
              <div key={i}>
                <p className="mb-2 text-sm font-medium text-ink-200">{item.question}</p>
                <AnswerCard
                  result={item.result}
                  onVerdictChange={(v) => handleVerdict(i, v)}
                />
              </div>
            ))}
          </div>
        </div>

        <div className="border-t border-ink-700 bg-ink-900/60 px-8 py-4">
          {error && <p className="mb-2 font-mono text-xs text-bad">{error}</p>}
          <div className="mx-auto max-w-3xl">
            <QuestionForm onAsk={handleAsk} disabled={asking} />
          </div>
        </div>
      </main>
    </div>
  );
}
