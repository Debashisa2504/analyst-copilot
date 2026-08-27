import { useState } from "react";
import { api } from "../api.js";

export default function FeedbackRow({ evidence, question }) {
  const [sent, setSent] = useState(null);

  const chunkIds = (evidence || [])
    .map((e) => e.chunk_id)
    .filter(Boolean);

  async function submit(verdict) {
    try {
      await api.sendFeedback(chunkIds, verdict);
      setSent(verdict);
    } catch (_) {
      setSent("error");
    }
  }

  if (sent === "correct") return <p className="mt-2 font-mono text-[11px] text-good">✓ Marked correct — thanks!</p>;
  if (sent === "wrong")   return <p className="mt-2 font-mono text-[11px] text-bad">✗ Marked wrong — improving retrieval…</p>;
  if (sent === "clarify") return <p className="mt-2 font-mono text-[11px] text-warn">? Noted — stored for review.</p>;
  if (sent === "error")   return <p className="mt-2 font-mono text-[11px] text-bad">Could not save feedback.</p>;

  return (
    <div className="mt-3 flex items-center gap-2">
      <span className="font-mono text-[11px] text-ink-400">Was this helpful?</span>
      <button
        onClick={() => submit("correct")}
        className="rounded-sm border border-ink-600 bg-ink-800 px-2 py-0.5 font-mono text-[11px] text-good hover:border-good transition-colors"
      >
        👍 Yes
      </button>
      <button
        onClick={() => submit("wrong")}
        className="rounded-sm border border-ink-600 bg-ink-800 px-2 py-0.5 font-mono text-[11px] text-bad hover:border-bad transition-colors"
      >
        👎 No
      </button>
      <button
        onClick={() => submit("clarify")}
        className="rounded-sm border border-ink-600 bg-ink-800 px-2 py-0.5 font-mono text-[11px] text-warn hover:border-warn transition-colors"
      >
        🔄 Unclear
      </button>
    </div>
  );
}
