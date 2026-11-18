import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import BlockClassificationReview, { filterReviewBlocks, pendingBlockReviewCount } from "./BlockClassificationReview";

const blocks = [
  { block_id: 1, page: 1, text: "Picking is the simplest method.", category: "other", predicted_category: "other", decision_margin: 0.05, review_margin_threshold: 0.1, review_required: true },
  { block_id: 2, page: 2, text: "What is picking?", category: "assessment" },
  { block_id: 3, page: 2, text: "A mixture can be separated.", category: "lesson_content" },
];

describe("PDF block classification review", () => {
  it("shows flagged source text, page, margin and correction controls", () => {
    const html = renderToStaticMarkup(<BlockClassificationReview blocks={blocks} materialId={9} editable onSave={vi.fn()} />);
    expect(html).toContain("Picking is the simplest method.");
    expect(html).toContain("Page 1");
    expect(html).toContain("0.0500");
    expect(html).toContain("not a confidence percentage");
    expect(html).toContain("Save block review");
    expect(html).toContain('value="assessment"');
    expect(html).toContain('value="lesson_content"');
    expect(html).toContain("All blocks");
  });

  it("keeps old excluded blocks reviewable without invented scores", () => {
    const html = renderToStaticMarkup(<BlockClassificationReview materialId={1} editable blocks={[{ block_id: 1, page: 1, text: "Excluded source", category: "other" }]} />);
    expect(html).toContain("Excluded source");
    expect(html).toContain("No decision scores stored");
    expect(html).not.toContain("NaN");
  });

  it("does not offer writes after confirmation or finalization", () => {
    const html = renderToStaticMarkup(<BlockClassificationReview blocks={blocks} materialId={9} editable={false} />);
    expect(html).toContain("read-only");
    expect(html).not.toContain("Save block review");
  });

  it("does not count teacher-accepted low-margin predictions as pending", () => {
    expect(pendingBlockReviewCount(blocks)).toBe(1);
    expect(pendingBlockReviewCount([{ ...blocks[0], teacher_reviewed: true }])).toBe(0);
  });

  it("makes all categories available through filters without dropping source text", () => {
    expect(filterReviewBlocks(blocks, "excluded")).toEqual([blocks[0]]);
    expect(filterReviewBlocks(blocks, "assessment")).toEqual([blocks[1]]);
    expect(filterReviewBlocks(blocks, "flagged")).toEqual([blocks[0]]);
    expect(filterReviewBlocks(blocks, "all")).toEqual(blocks);
  });

  it("escapes source HTML instead of executing it", () => {
    const html = renderToStaticMarkup(<BlockClassificationReview materialId={1} blocks={[{ block_id: 1, text: "<script>bad()</script>", category: "other" }]} />);
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });
});
