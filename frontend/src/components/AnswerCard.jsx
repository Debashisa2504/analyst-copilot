import CitationBadge from "./CitationBadge.jsx";
import ConfidenceMeter from "./ConfidenceMeter.jsx";
import EvidenceViewer from "./EvidenceViewer.jsx";
import FeedbackRow from "./FeedbackRow.jsx";

export default function AnswerCard({ result, onVerdictChange }) {
  if (!result) return null;
  const {
    answer, abstained, doc_name, page_num,
    confidence, retrieval_agreement, evidence,
  } = result;

  return (
    <div className="animate-[fadeIn_.25s_ease] rounded-lg border border-ink-700 bg-ink-900 p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-[11px] uppercase tracking-wide text-ink-400">
          {doc_name}
        </span>
        <CitationBadge pageNum={page_num} abstained={abstained} />
      </div>

      <p className={`text-[15px] leading-relaxed ${abstained ? "italic text-ink-400" : "text-ink-100"}`}>
        {abstained
          ? "Not found in this filing — evidence was insufficient or inconsistent."
          : answer}
      </p>

      <div className="mt-4 space-y-2 border-t border-ink-700 pt-4">
        <ConfidenceMeter value={confidence} label="Confidence" />
        <ConfidenceMeter value={retrieval_agreement} label="Agreement" />
      </div>

      <EvidenceViewer evidence={evidence} />

      <FeedbackRow
        evidence={evidence}
        onVerdict={(v) => onVerdictChange?.(v)}
      />
    </div>
  );
}
