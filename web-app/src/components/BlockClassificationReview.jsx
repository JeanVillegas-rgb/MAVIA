import { useState } from "react";

const CATEGORIES = { lesson_content: "Lesson content", assessment: "Assessment", other: "Other / excluded" };

export function pendingBlockReviewCount(blocks = []) {
  return blocks.filter((block) => block.review_required && !block.teacher_reviewed).length;
}

export function filterReviewBlocks(blocks, filter) {
  if (filter === "flagged") return blocks.filter((block) => block.review_required && !block.teacher_reviewed);
  if (filter === "excluded") return blocks.filter((block) => block.category === "other");
  if (filter === "assessment") return blocks.filter((block) => block.category === "assessment");
  return blocks;
}

function BlockReviewRow({ block, materialId, editable, busy, onSave }) {
  const [category, setCategory] = useState(block.category in CATEGORIES ? block.category : "other");
  const labelId = `block-category-${materialId}-${block.block_id}`;
  const hasMargin = Number.isFinite(block.decision_margin);
  return (
    <article className="block-review-row">
      <strong>Block {block.block_id} · Page {block.page ?? "unknown"}</strong>
      <p className="muted-text">
        Predicted: {CATEGORIES[block.predicted_category || block.category] || "Unknown"}
        {block.docling_label ? ` · ${block.docling_label}` : ""}
        {block.teacher_reviewed ? " · Teacher reviewed" : block.review_required ? " · Needs review" : ""}
      </p>
      <p className="block-review-text">{block.text}</p>
      <p className="muted-text">
        {hasMargin ? `Decision margin: ${block.decision_margin.toFixed(4)} (not a confidence percentage). ` : "No decision scores stored for this upload. "}
        {Number.isFinite(block.review_margin_threshold)
          ? `Review cutoff: ${block.review_margin_threshold}.`
          : "Automatic margin cutoff is off or was not recorded."}
      </p>
      {editable && Number.isInteger(block.block_id) && (
        <div className="block-review-actions">
          <label htmlFor={labelId}>Category</label>
          <select id={labelId} value={category} disabled={busy}
            onChange={(event) => setCategory(event.target.value)}>
            {Object.entries(CATEGORIES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <button type="button" className="btn btn-secondary btn-small" disabled={busy}
            onClick={() => onSave(block.block_id, category)}>
            Save block review
          </button>
        </div>
      )}
    </article>
  );
}

export default function BlockClassificationReview({ blocks = [], materialId, editable, busy, onSave }) {
  const pending = pendingBlockReviewCount(blocks);
  const [filter, setFilter] = useState(pending ? "flagged" : "excluded");
  if (!blocks.length) return null;
  const visible = filterReviewBlocks(blocks, filter);
  return (
    <details className="block-classification-review" open={pending > 0 || undefined}>
      <summary>PDF block review · {pending} flagged · {blocks.filter((b) => b.category === "other").length} excluded</summary>
      <p>Review model predictions, including excluded text. A low decision margin is a review hint, not proof of an error.</p>
      <p className="muted-text">
        Corrections preserve existing edits. Source-linked objects/questions must be reviewed or removed through their own controls before changing category.
        Heading corrections affect subsequent restorations; existing object titles are not rewritten.
      </p>
      {!editable && <p role="status">Block review is read-only after confirmation or source finalization.</p>}
      <label htmlFor={`block-filter-${materialId}`}>Show blocks</label>{" "}
      <select id={`block-filter-${materialId}`} value={filter} onChange={(event) => setFilter(event.target.value)}>
        <option value="excluded">Excluded</option>
        <option value="flagged">Needs review</option>
        <option value="assessment">Assessment</option>
        <option value="all">All blocks</option>
      </select>
      <div className="block-review-list">
        {!visible.length && <p>No blocks in this filter. Choose All blocks to inspect the source text.</p>}
        {visible.map((block) => <BlockReviewRow
          key={`${block.block_id}:${block.category}:${block.reviewed_at || ""}`}
          block={block} materialId={materialId} editable={editable} busy={busy} onSave={onSave}
        />)}
      </div>
    </details>
  );
}
