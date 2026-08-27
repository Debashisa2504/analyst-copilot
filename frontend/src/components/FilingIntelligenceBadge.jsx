import { useEffect, useState } from "react";
import { api } from "../api.js";

const SEV_COLOR = { critical: "text-bad", high: "text-bad", medium: "text-warn", low: "text-ink-400" };

export default function FilingIntelligenceBadge({ docName }) {
  const [intel, setIntel] = useState(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!docName || docName === "ALL") return;
    api.getIntelligence(docName).then(setIntel).catch(() => {});
  }, [docName]);

  if (!intel || docName === "ALL") return null;

  const isPending = intel.status === "pending";
  const redFlagCount = (intel.red_flags || []).length;
  const tone = intel.tone_score ?? null;
  const conviction = intel.conviction;

  return (
    <div className="mt-3 rounded-md border border-ink-700 bg-ink-900 text-[11px] font-mono">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-3 py-2 text-left hover:bg-ink-800/60 transition-colors"
      >
        <span className="uppercase tracking-wide text-ink-400">Filing Intelligence</span>
        <span className="text-ink-400">{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="border-t border-ink-700 px-3 py-2 space-y-2">
          {isPending ? (
            <p className="text-ink-400 animate-pulse">Enriching… check back shortly.</p>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <span className="text-ink-400">Tone</span>
                <span className={tone >= 60 ? "text-good" : tone >= 40 ? "text-warn" : "text-bad"}>
                  {tone}/100
                </span>
              </div>
              {conviction && (
                <div className="flex items-center justify-between">
                  <span className="text-ink-400">Conviction</span>
                  <span className={conviction.score >= 65 ? "text-good" : conviction.score >= 45 ? "text-warn" : "text-bad"}>
                    {conviction.score}/100 ({conviction.label})
                  </span>
                </div>
              )}
              <div className="flex items-center justify-between">
                <span className="text-ink-400">Red Flags</span>
                <span className={redFlagCount > 3 ? "text-bad" : redFlagCount > 0 ? "text-warn" : "text-good"}>
                  {redFlagCount}
                </span>
              </div>
              {redFlagCount > 0 && (
                <ul className="mt-1 space-y-1 border-t border-ink-700 pt-2">
                  {(intel.red_flags || []).map((f, i) => (
                    <li key={i} className={SEV_COLOR[f.severity] || "text-ink-400"}>
                      [{f.severity.toUpperCase()}] {f.description}
                    </li>
                  ))}
                </ul>
              )}
              {conviction?.disclaimer && (
                <p className="mt-2 text-ink-600 text-[10px] border-t border-ink-700 pt-2">
                  {conviction.disclaimer}
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
