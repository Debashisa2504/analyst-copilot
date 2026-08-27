import { useState } from "react";

const TYPE_LABEL = {
  prose: "PROSE",
  table_row: "TABLE FACT",
  footnote: "FOOTNOTE",
  table: "TABLE",
};

function EvidenceRow({ item, index }) {
  const [open, setOpen] = useState(index === 0);
  return (
    <div className="border-b border-ink-700 last:border-b-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left transition-colors hover:bg-ink-800/60"
      >
        <div className="flex items-center gap-3 overflow-hidden">
          <span className="shrink-0 rounded-sm bg-ink-700 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-ink-200">
            {TYPE_LABEL[item.chunk_type] || item.chunk_type}
          </span>
          <span className="truncate font-mono text-xs text-ink-400">
            p.{item.page_num} · {item.doc_name}
          </span>
        </div>
        <span className="shrink-0 font-mono text-xs text-ink-400">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div className="px-4 pb-3">
          <p className="whitespace-pre-wrap font-mono text-[13px] leading-relaxed text-ink-100">
            {item.text}
          </p>
        </div>
      )}
    </div>
  );
}

export default function EvidenceViewer({ evidence }) {
  if (!evidence || evidence.length === 0) return null;
  return (
    <div className="mt-4 overflow-hidden rounded-md border border-ink-700 bg-ink-900">
      <div className="border-b border-ink-700 bg-ink-800/60 px-4 py-2">
        <span className="font-mono text-[11px] uppercase tracking-wide text-ink-400">
          Grounding evidence · {evidence.length} excerpt{evidence.length > 1 ? "s" : ""}
        </span>
      </div>
      {evidence.map((item, i) => (
        <EvidenceRow key={i} item={item} index={i} />
      ))}
    </div>
  );
}
