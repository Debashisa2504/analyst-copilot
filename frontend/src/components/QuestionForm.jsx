import { useState } from "react";

export default function QuestionForm({ onAsk, disabled }) {
  const [value, setValue] = useState("");

  function submit(e) {
    e.preventDefault();
    const q = value.trim();
    if (!q) return;
    onAsk(q);
  }

  return (
    <form onSubmit={submit} className="flex items-end gap-3">
      <div className="flex-1">
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) submit(e);
          }}
          rows={2}
          placeholder="Ask about the filing — e.g. “What was FY2018 capital expenditure?”"
          className="w-full resize-none rounded-md border border-ink-700 bg-ink-800 px-4 py-3 text-sm text-ink-100 placeholder:text-ink-400 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
        />
      </div>
      <button
        type="submit"
        disabled={disabled}
        className="h-11 shrink-0 rounded-md bg-accent px-5 text-sm font-medium text-white transition-colors hover:bg-accent-dim disabled:cursor-not-allowed disabled:opacity-40"
      >
        {disabled ? "Thinking…" : "Ask"}
      </button>
    </form>
  );
}
