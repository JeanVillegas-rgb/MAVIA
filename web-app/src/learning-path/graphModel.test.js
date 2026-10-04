import { describe, expect, it } from "vitest";

import { buildGraph, classifyDrop, conceptsToReview, highlightRoles, pathEdges, splitLinked } from "./graphModel";

const link = (linkId, conceptId, title, extra = {}) => ({ link_id: linkId, concept_id: conceptId, title, ...extra });
const step = (id, position, title, prerequisites = [], suggestions = []) => ({
  concept_id: id, position, title, kind: "text", content: "", prerequisites, suggestions,
});

const STEPS = [
  step(1, 1, "Matter"),
  step(2, 2, "Solid", [link(10, 1, "Matter")]),
  step(3, 3, "Liquid", [link(11, 1, "Matter")], [link(20, 2, "Solid", { reason: "Liquid's text names Solid in 1 of 1 passages; Solid's text never names Liquid.", cross_section: true })]),
  step(4, 4, "Summary"),
];

const at = (graph, id) => graph.nodes.find((node) => node.id === String(id)).position;

describe("buildGraph", () => {
  it("puts a prerequisite above its dependent", () => {
    const graph = buildGraph(STEPS);
    expect(at(graph, 1).y).toBeLessThan(at(graph, 2).y);
  });

  it("puts siblings side by side on one row", () => {
    const graph = buildGraph(STEPS);
    expect(at(graph, 2).y).toBe(at(graph, 3).y);
    expect(at(graph, 2).x).not.toBe(at(graph, 3).x);
  });

  it("puts unlinked concepts in a labelled strip below the graph", () => {
    const graph = buildGraph(STEPS);
    expect(at(graph, 4).y).toBeGreaterThan(at(graph, 2).y);
    expect(graph.nodes.some((node) => node.id === "not-linked-label")).toBe(true);
  });

  it("draws one arrow per path link, prerequisite to dependent", () => {
    const graph = buildGraph(STEPS);
    expect(graph.edges).toHaveLength(2);
    expect(graph.edges[0]).toMatchObject({ id: "link-10", source: "1", target: "2" });
  });

  it("every concept sits in the strip when there are no links", () => {
    const bare = [step(1, 1, "A"), step(2, 2, "B")];
    const graph = buildGraph(bare);
    expect(graph.edges).toEqual([]);
    expect(at(graph, 1).y).toBe(at(graph, 2).y);
    expect(at(graph, 1).x).toBeLessThan(at(graph, 2).x);
  });

  it("dims every concept unrelated to the selection", () => {
    const graph = buildGraph(STEPS, 2);
    const role = (id) => graph.nodes.find((node) => node.id === String(id)).data.role;
    expect(role(2)).toBe("is-selected");
    expect(role(1)).toBe("is-needed");
    expect(role(4)).toBe("is-dimmed");
  });
});

describe("highlightRoles", () => {
  it("marks what builds on the selected concept", () => {
    const roles = highlightRoles(STEPS, 1);
    expect(roles.get(2)).toBe("is-leads");
    expect(roles.get(3)).toBe("is-leads");
  });

  it("is empty with nothing selected", () => {
    expect(highlightRoles(STEPS, null).size).toBe(0);
  });
});

describe("splitLinked", () => {
  it("keeps teaching order in both groups", () => {
    const { linked, unlinked } = splitLinked(STEPS);
    expect(linked.map((s) => s.concept_id)).toEqual([1, 2, 3]);
    expect(unlinked.map((s) => s.concept_id)).toEqual([4]);
  });
});

describe("classifyDrop", () => {
  it("ignores a concept dropped on itself", () => {
    expect(classifyDrop(STEPS, 2, 2).kind).toBe("self");
  });

  it("says so when the target already comes first", () => {
    expect(classifyDrop(STEPS, 2, 1).kind).toBe("already");
  });

  it("adds when the dragged concept has no prerequisite", () => {
    expect(classifyDrop(STEPS, 4, 1)).toEqual({ kind: "add", current: [] });
  });

  it("asks add-or-move when the dragged concept already has one", () => {
    const drop = classifyDrop(STEPS, 2, 3);
    expect(drop.kind).toBe("choose");
    expect(drop.current.map((entry) => entry.title)).toEqual(["Matter"]);
  });
});

describe("pending recommendations on the graph", () => {
  it("counts each concept's pending recommendations on its node", () => {
    const graph = buildGraph(STEPS);
    const pending = (id) => graph.nodes.find((node) => node.id === String(id)).data.pending;
    expect(pending(3)).toBe(1);
    expect(pending(2)).toBe(0);
  });

  it("counts the concepts that have something to review", () => {
    expect(conceptsToReview(STEPS)).toBe(1);
    expect(conceptsToReview([step(1, 1, "A")])).toBe(0);
  });
});

describe("pathEdges", () => {
  it("leaves out links a longer chain already implies", () => {
    const steps = [
      step(1, 1, "Matter"),
      step(2, 2, "Solid", [link(10, 1, "Matter")]),
      step(3, 3, "Liquid", [link(11, 2, "Solid"), link(12, 1, "Matter", { redundant: true })]),
    ];

    expect(pathEdges(steps).map((edge) => edge.linkId)).toEqual([10, 11]);
  });
});
