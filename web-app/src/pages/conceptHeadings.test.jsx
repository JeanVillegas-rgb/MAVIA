import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ConceptObjectPreview } from "./TopicDetailPage";
import BundleParts from "../learning-path/BundleParts";
import { objectHeadings } from "../learning-path/objectHeadings";

const objects = [
  "Matter has mass and occupies space.",
  "1. Solid - A solid keeps its shape.",
  "2. Liquid - A liquid flows.",
  "3. Gas - A gas fills its container.",
].map((content, index) => ({
  id: index + 1, kind: "text", title: "Matter", section_title: "Matter", content,
}));

function preview(items, conceptTitle = "Matter") {
  return renderToStaticMarkup(<section>
    <h4>{conceptTitle}</h4>
    {items.map((item, index) => <ConceptObjectPreview
      key={item.id}
      item={item}
      previousItem={items[index - 1]}
      conceptTitle={conceptTitle}
      number={`2.1.${index + 1}`}
    />)}
  </section>);
}

describe("concept section headings", () => {
  it("shows Matter once above four independently numbered objects", () => {
    const before = JSON.stringify(objects);
    const html = preview(objects);
    expect(html).toContain("<h4>Matter</h4>");
    expect(html).not.toContain("<strong>Matter</strong>");
    expect(html).not.toContain("Section: Matter");
    expect(html.match(/class="connection-object-row"/g)).toHaveLength(4);
    for (let index = 0; index < objects.length; index += 1) {
      expect(html).toContain(`>2.1.${index + 1}</span>`);
      expect(html).toContain(objects[index].content.replace(/^\d\. /, ""));
    }
    expect(html).toContain(">3.</span>");
    expect(JSON.stringify(objects)).toBe(before);
  });

  it("keeps distinct object titles under a shared heading", () => {
    const html = preview(objects.map((item, index) => ({
      ...item, title: ["Matter", "Solid", "Liquid", "Gas"][index],
    })));
    for (const title of ["Solid", "Liquid", "Gas"]) {
      expect(html).toContain(`<strong>${title}</strong>`);
    }
    expect(html).not.toContain("<strong>Matter</strong>");
  });

  it("shows a section once when the parent concept has a different name", () => {
    const html = preview([
      ...objects,
      { id: 5, title: "Examples", section_title: "Examples", content: "Ice is solid." },
    ], "States of matter");
    expect(html.match(/<h6>Matter<\/h6>/g)).toHaveLength(1);
    expect(html.match(/<h6>Examples<\/h6>/g)).toHaveLength(1);
    expect(html).toContain("Ice is solid.");
  });

  it("compares case and whitespace without changing source titles", () => {
    expect(objectHeadings(
      { title: " MATTER ", section_title: " Matter " }, null, "matter",
    )).toEqual({ title: "", section: "" });
    expect(objectHeadings(
      { title: "Particle   arrangement", section_title: "Matter" },
      { title: "Particle arrangement", section_title: "matter" }, "Matter",
    )).toEqual({ title: "", section: "" });
  });

  it("does not hide equal titles in different sections", () => {
    expect(objectHeadings(
      { title: "Examples", section_title: "Liquids" },
      { title: "Examples", section_title: "Solids" }, "States",
    )).toEqual({ title: "Examples", section: "Liquids" });
  });

  it("retains figures and missing-description notices without redundant headings", () => {
    const html = preview([{
      id: 1, kind: "image", title: "Matter", section_title: "Matter", content: "",
      image_url: "/diagram.png", narration_pending: true,
    }]);
    expect(html).toContain('src="/diagram.png"');
    expect(html).toContain('alt="Matter"');
    expect(html).toContain("Narration pending:");
    expect(html).not.toContain("<strong>Matter</strong>");
  });
});

describe("content-version bundle headings", () => {
  const parts = objects.map(({ content, ...item }) => ({ ...item, text: content }));

  it("omits repeated parent headings but keeps every part's text and order", () => {
    const html = renderToStaticMarkup(<BundleParts parts={parts} parentTitle="Matter" showSingleTitle />);
    expect(html.match(/class="bundle-part"/g)).toHaveLength(4);
    expect(html).not.toContain("Matter</strong>");
    expect(html).not.toContain("<h6>");
    expect(html).not.toMatch(/<strong[^>]*>\d+\./);
    for (const part of parts) expect(html).toContain(part.text.replace(/^\d\. /, ""));
  });

  it("keeps one heading if no parent heading is supplied", () => {
    const html = renderToStaticMarkup(<BundleParts parts={parts} />);
    expect(html.match(/<h6>Matter<\/h6>/g)).toHaveLength(1);
  });

  it("preserves distinct part titles and the single-part/fallback behavior", () => {
    const html = renderToStaticMarkup(<BundleParts parts={[
      { id: 1, title: "Solid", text: "Keeps its shape." },
      { id: 2, title: "Liquid", text: "Flows." },
    ]} parentTitle="Matter" />);
    expect(html).toContain('<strong class="bundle-part-title">Solid</strong>');
    expect(html).toContain('<strong class="bundle-part-title">Liquid</strong>');
    expect(renderToStaticMarkup(<BundleParts parts={[parts[0]]} />)).toContain(parts[0].text);
    expect(renderToStaticMarkup(<BundleParts parts={[]} fallback="Saved wording." />)).toContain("Saved wording.");
  });
});

describe("source numbering in content versions", () => {
  const render = (parts, props = {}) => renderToStaticMarkup(
    <BundleParts parts={parts} parentTitle="Matter" showSingleTitle {...props} />,
  );

  it("renders an introductory paragraph then only the original three numbers", () => {
    const html = render(objects.map(({ content, ...item }) => ({ ...item, text: content })));
    expect(html).toContain("<p>Matter has mass and occupies space.</p>");
    expect(html.match(/<li[ >]/g)).toHaveLength(3);
    for (const [marker, text] of [
      ["1.", "Solid - A solid keeps its shape."],
      ["2.", "Liquid - A liquid flows."],
      ["3.", "Gas - A gas fills its container."],
    ]) {
      // The source marker and text occupy adjacent columns of the same row.
      expect(html).toContain(`<span>${marker}</span><div>${text}</div>`);
    }
    expect(html).not.toContain("<strong>");
    expect(html).not.toContain("4.");
  });

  it("does not manufacture numbers for unnumbered comparing-state paragraphs", () => {
    const parts = ["Shape: Solids keep their shape.", "Volume: Solids have volume.", "Flow: Liquids flow."]
      .map((text) => ({ title: "Matter", text }));
    const html = render(parts);
    expect(html).not.toContain("<ol");
    expect(html).not.toContain("<li");
    expect(html).not.toContain("<strong>");
    for (const part of parts) expect(html).toContain(`<p>${part.text}</p>`);
  });

  it("keeps authored numbered titles without adding a second number", () => {
    const html = render([
      { title: "2. Solids", text: "Particles are packed tightly." },
      { title: "Matter", text: "Diagram description: a fixed arrangement." },
    ]);
    expect(html).toContain('<strong class="bundle-part-title">2. Solids</strong>');
    expect(html).not.toContain("1. 2. Solids");
    expect(html).not.toContain("<strong>2.</strong>");
    expect(html).toContain("Diagram description: a fixed arrangement.");
  });

  it("preserves bullet and nested list markers rather than numbering every object", () => {
    const html = render([{ title: "Matter", text: "• Parent\n  a) Child\n    iv. Detail\n• Sibling" }]);
    for (const marker of ["•", "a)", "iv."]) expect(html).toContain(`<span>${marker}</span>`);
    expect(html.match(/<li[ >]/g)).toHaveLength(4);
    expect(html.indexOf("Child")).toBeLessThan(html.indexOf("</li>"));
  });

  it("supports compact source numbering and does not reinterpret decimals", () => {
    const html = render([{ title: "Matter", text: "1.Solid\n2.Liquid\n3.Gas\n3.14 units." }]);
    expect(html).toContain("<span>1.</span><div>Solid</div>");
    expect(html).toContain("<span>2.</span><div>Liquid</div>");
    expect(html).toContain("<span>3.</span><div>Gas</div>");
    expect(html).toContain("<p>3.14 units.</p>");
  });

  it("formats fallback and single-part text using the same source-only rules", () => {
    for (const html of [
      render([{ title: "Matter", text: "7. A source item." }], { showSingleTitle: false }),
      render([], { fallback: "7. A source item." }),
    ]) {
      expect(html).toContain("<span>7.</span><div>A source item.</div>");
      expect(html).not.toContain("<span>1.</span>");
    }
  });

  it("keeps bold inline content and escapes source HTML", () => {
    const html = render([{ title: "Matter", text: "1. **Solid** <script>bad()</script>" }]);
    expect(html).toContain("<span>1.</span><div><strong>Solid</strong> &lt;script&gt;");
    expect(html).not.toContain("<script>");
  });
});
