import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import ConceptGroupingControls from "./ConceptGroupingControls";
import { moveConceptObject, separateConceptObject } from "../api";

afterEach(() => vi.unstubAllGlobals());
const group = { id: 1, label: "Matter" };
const target = { id: 2, label: "Solid" };
const props = { item: { id: 10, title: "Matter" }, group, groups: [group, target] };

describe("manual concept correction", () => {
  it("offers a named separation and excludes the current group from destinations", () => {
    const html = renderToStaticMarkup(<ConceptGroupingControls {...props} />);
    expect(html).toContain("Separate into own concept");
    expect(html).toContain("Move to concept");
    expect(html).toContain('value="2"');
    expect(html).not.toContain('value="1"');
    expect(html).toContain("This does not split paragraphs.");
  });

  it("disables corrections during an outstanding request", () => {
    const html = renderToStaticMarkup(<ConceptGroupingControls {...props} busy />);
    expect(html).toContain('<fieldset disabled="">');
  });

  it("does not offer a move when there is no other concept", () => {
    const html = renderToStaticMarkup(<ConceptGroupingControls {...props} groups={[group]} />);
    expect(html).not.toContain("Move to concept");
    expect(html).toContain("Separate into own concept");
  });

  it("posts exactly one object and the teacher-selected destination", async () => {
    vi.stubGlobal("localStorage", { getItem: () => "test-token" });
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ learning_object_groups: [] })));
    vi.stubGlobal("fetch", fetch);
    await moveConceptObject(2, 3, 10, "7");
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe("/api/courses/2/outline-nodes/3/connect-learning-objects/");
    expect(JSON.parse(options.body)).toEqual({ learning_object_ids: [10], target_group_id: 7 });
    expect(options.method).toBe("POST");
  });

  it("sends the separate concept name and surfaces errors", async () => {
    vi.stubGlobal("localStorage", { getItem: () => null });
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Sources finalized" }), { status: 409 }));
    vi.stubGlobal("fetch", fetch);
    await expect(separateConceptObject(2, 3, 10, "Solid")).rejects.toThrow("Sources finalized");
    expect(fetch.mock.calls[0][0]).toBe("/api/courses/2/outline-nodes/3/separate-learning-object/");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ learning_object_id: 10, label: "Solid" });
  });
});
