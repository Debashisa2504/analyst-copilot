/**
 * ScoreDashboard — live running FinanceBench-style +1/0/-1 tally.
 * Tallies: correct (+1), abstained (0), wrong (-1) from user feedback.
 */
export default function ScoreDashboard({ history }) {
  const correct = history.filter((h) => !h.result?.abstained && h.verdict === "correct").length;
  const abstained = history.filter((h) => h.result?.abstained).length;
  const wrong = history.filter((h) => h.verdict === "wrong").length;
  const total = history.length;
  const score = correct - wrong;
  const precision = total > 0 ? ((correct / (correct + wrong || 1)) * 100).toFixed(0) : "—";

  if (total === 0) return null;

  return (
    <div className="mt-4 rounded-md border border-ink-700 bg-ink-900 px-4 py-3 font-mono text-[11px]">
      <p className="uppercase tracking-wide text-ink-400 mb-2">Session Score</p>
      <div className="flex gap-4">
        <span className="text-good">+{correct} correct</span>
        <span className="text-ink-400">{abstained} abstained</span>
        <span className="text-bad">−{wrong} wrong</span>
      </div>
      <div className="mt-1 flex gap-4 text-ink-200">
        <span>Net: {score >= 0 ? "+" : ""}{score}</span>
        <span>Precision: {precision}%</span>
        <span>Queries: {total}</span>
      </div>
    </div>
  );
}
