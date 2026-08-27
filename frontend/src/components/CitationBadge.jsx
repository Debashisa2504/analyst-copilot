export default function CitationBadge({ pageNum, abstained }) {
  if (abstained) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-sm border border-ink-600 bg-ink-800 px-2 py-1 font-mono text-xs text-ink-400">
        NO CITATION
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-sm border border-accent-dim/60 bg-accent-dim/10 px-2 py-1 font-mono text-xs text-accent">
      <span className="h-1.5 w-1.5 rounded-full bg-accent" />
      SEC EDGAR&nbsp;Page&nbsp;{pageNum ?? "—"}
    </span>
  );
}
