import { useRef, useState } from "react";
import { api } from "../api.js";
import FilingIntelligenceBadge from "./FilingIntelligenceBadge.jsx";

export default function Sidebar({ filings, scope, onScopeChange, onFilingsChanged, health }) {
  const inputRef = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [lastUpload, setLastUpload] = useState(null);

  async function handleFiles(fileList) {
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    try {
      const res = await api.uploadFilings(Array.from(fileList));
      setLastUpload(res.results);
      await onFilingsChanged();
    } catch (err) {
      setLastUpload([{ filename: "upload", status: `error: ${err.message}` }]);
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-ink-700 bg-ink-900">
      <div className="border-b border-ink-700 px-5 py-5">
        <h1 className="font-mono text-sm font-semibold tracking-tight text-ink-100">
          THE ANALYST COPILOT
        </h1>
        <p className="mt-1 text-xs text-ink-400">Grounded SEC filing QA · PRISM</p>
        <div className="mt-3 flex items-center gap-2">
          <span className={`h-1.5 w-1.5 rounded-full ${health?.status === "ready" ? "bg-good" : "bg-warn"}`} />
          <span className="font-mono text-[11px] text-ink-400">
            {health?.indexed_count ?? 0} filing{health?.indexed_count === 1 ? "" : "s"} indexed
          </span>
        </div>
      </div>

      <div className="border-b border-ink-700 px-5 py-4">
        <label className="mb-2 block font-mono text-[11px] uppercase tracking-wide text-ink-400">
          Filing scope
        </label>
        <select
          value={scope}
          onChange={(e) => onScopeChange(e.target.value)}
          className="w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-accent focus:outline-none"
        >
          <option value="ALL">All indexed filings</option>
          {filings.map((f) => (
            <option key={f} value={f}>{f}</option>
          ))}
        </select>

        <FilingIntelligenceBadge docName={scope} />
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        <label className="mb-2 block font-mono text-[11px] uppercase tracking-wide text-ink-400">
          Upload filings
        </label>
        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); handleFiles(e.dataTransfer.files); }}
          onClick={() => inputRef.current?.click()}
          className="cursor-pointer rounded-md border border-dashed border-ink-600 bg-ink-800/40 px-3 py-6 text-center transition-colors hover:border-accent"
        >
          <p className="text-xs text-ink-400">
            Drop .htm / .html filings here, or click to browse
          </p>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".htm,.html,.txt"
            className="hidden"
            onChange={(e) => handleFiles(e.target.files)}
          />
        </div>

        {uploading && (
          <p className="mt-2 font-mono text-[11px] text-accent animate-pulse">
            Parsing & indexing…
          </p>
        )}

        {lastUpload && (
          <ul className="mt-3 space-y-1">
            {lastUpload.map((r, i) => (
              <li key={i} className="truncate font-mono text-[11px] text-ink-400">
                {r.filename}: <span className="text-ink-200">{r.status}</span>
                {r.chunks && (
                  <span className="text-ink-600"> ({r.chunks} chunks)</span>
                )}
              </li>
            ))}
          </ul>
        )}

        <div className="mt-6">
          <label className="mb-2 block font-mono text-[11px] uppercase tracking-wide text-ink-400">
            Indexed filings ({filings.length})
          </label>
          <ul className="space-y-1">
            {filings.map((f) => (
              <li
                key={f}
                onClick={() => onScopeChange(f)}
                className={`truncate cursor-pointer font-mono text-[11px] transition-colors ${
                  scope === f ? "text-accent" : "text-ink-400 hover:text-ink-200"
                }`}
              >
                {f}
              </li>
            ))}
            {filings.length === 0 && (
              <li className="font-mono text-[11px] text-ink-600">No filings yet</li>
            )}
          </ul>
        </div>
      </div>
    </aside>
  );
}
