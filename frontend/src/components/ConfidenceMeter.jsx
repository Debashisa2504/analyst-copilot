export default function ConfidenceMeter({ value, label }) {
  const pct = Math.round((value ?? 0) * 100);
  const tone = pct >= 75 ? "bg-good" : pct >= 50 ? "bg-warn" : "bg-bad";
  return (
    <div className="flex items-center gap-2">
      <span className="w-24 shrink-0 font-mono text-[11px] uppercase tracking-wide text-ink-400">
        {label}
      </span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-700">
        <div className={`h-full ${tone} transition-all duration-500`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-10 shrink-0 text-right font-mono text-[11px] text-ink-200">{pct}%</span>
    </div>
  );
}
