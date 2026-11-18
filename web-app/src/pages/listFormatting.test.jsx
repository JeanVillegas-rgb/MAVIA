import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FormattedLearningObjectContent } from "./TopicDetailPage";

const render = (content) => renderToStaticMarkup(
  <FormattedLearningObjectContent content={content} />,
);

describe("source list formatting", () => {
  it("preserves original numbering, letters, Roman numerals and bullet symbols", () => {
    for (const marker of ["3.", "(4)", "b)", "iv.", "•", "◦", "+"]) {
      const html = render(`${marker} An item.`);
      expect(html).toContain("<li");
      expect(html).toContain(`>${marker}</span>`);
      expect(html).toContain("An item.");
    }
  });

  it("renders nested lists inside the parent item", () => {
    const html = render("• Parent\n  a) Child\n    iv. Grandchild\n• Sibling");
    expect(html.match(/<li[ >]/g)).toHaveLength(4);
    expect(html.match(/<ol[ >]/g)).toHaveLength(2);
    expect(html.indexOf("Child")).toBeLessThan(html.indexOf("</li>"));
  });

  it("does not turn prose, decimals or text after a heading into guessed lists", () => {
    const html = render("Properties:\nA normal paragraph.\nA measurement is 3.14 units.");
    expect(html).not.toContain("<li");
    expect(html).toContain("<strong>Properties:</strong>");
    expect(html).toContain("3.14 units.");
  });

  it("keeps bold text and escapes HTML rather than executing it", () => {
    const html = render("1. **Solid** <script>bad()</script>");
    expect(html).toContain("<strong>Solid</strong>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
  });
});
