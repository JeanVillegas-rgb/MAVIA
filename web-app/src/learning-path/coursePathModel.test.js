import { describe, expect, it } from "vitest";

import { arrowKind, buildCourseGraph, learningOrder } from "./coursePathModel";

const PATH = {
  course: { id: 1, title: "Grade 1 Science" },
  topics: [
    { id: 10, title: "Solid, Liquid and Gas", position: 0, has_content: true },
    { id: 11, title: "Grouping Materials", position: 1, has_content: true },
    { id: 12, title: "Empty topic", position: 2, has_content: false },
  ],
  arrows: [
    { from_topic: 10, to_topic: 11, contradicts_outline: false, shaping: 2, pending: 1, links: [] },
    { from_topic: 11, to_topic: 10, contradicts_outline: true, shaping: 0, pending: 1, links: [] },
  ],
};

describe("arrowKind", () => {
  it("names an arrow by what the teacher must know first", () => {
    expect(arrowKind(PATH.arrows[0])).toBe("follows");
    expect(arrowKind(PATH.arrows[1])).toBe("contradicts");
    expect(arrowKind({ contradicts_outline: false, shaping: 0, pending: 2 })).toBe("pending");
  });
});

const LAYOUT_PATH = {
  topics: [
    { id: 10, title: "Solid, Liquid and Gas", position: 0, has_content: true },
    { id: 11, title: "Mixtures", position: 1, has_content: true },
    { id: 12, title: "Weather", position: 2, has_content: true },
    { id: 13, title: "Empty topic", position: 3, has_content: false },
  ],
  arrows: [{ from_topic: 10, to_topic: 11, contradicts_outline: false, shaping: 1, pending: 0, links: [] }],
};

describe("buildCourseGraph", () => {
  it("flows top to bottom: an earlier topic sits above the topic that builds on it", () => {
    const graph = buildCourseGraph(LAYOUT_PATH);
    const at = (id) => graph.nodes.find((node) => node.id === id).position;
    expect(at("10").y).toBeLessThan(at("11").y);
  });

  it("puts topics without arrows in a strip below, under a label", () => {
    const graph = buildCourseGraph(LAYOUT_PATH);
    const at = (id) => graph.nodes.find((node) => node.id === id).position;
    expect(at("12").y).toBeGreaterThan(at("11").y);
    expect(graph.nodes.find((node) => node.id === "13").data.empty).toBe(true);
    expect(graph.nodes.some((node) => node.type === "label")).toBe(true);
  });

  it("lays out a course with no arrows at all", () => {
    const graph = buildCourseGraph({ ...LAYOUT_PATH, arrows: [] });
    expect(graph.nodes.filter((node) => node.type === "topic")).toHaveLength(4);
    expect(graph.edges).toEqual([]);
  });

  it("labels each arrow with its link count", () => {
    const graph = buildCourseGraph(PATH);
    expect(graph.edges.map((edge) => edge.label)).toEqual(["2 links", "1 suggestion"]);
    expect(graph.edges[1].className).toContain("contradicts");
  });
});

const ORDER_PATH = {
  topics: [
    { id: 11, title: "Mixtures", position: 1, has_content: true, published: false,
      steps: [{ concept_id: 5, title: "Solutions", position: 1 }, { concept_id: 6, title: "Air", position: 2 }] },
    { id: 10, title: "Solid, Liquid and Gas", position: 0, has_content: true, published: true,
      steps: [{ concept_id: 1, title: "Liquid", position: 1 }] },
    { id: 12, title: "Empty", position: 2, has_content: false, published: false, steps: [] },
  ],
  arrows: [{
    from_topic: 10, to_topic: 11, contradicts_outline: false, shaping: 1, pending: 2,
    links: [
      { id: 1, status: "approved", rank: 1, prerequisite: { concept_id: 1, topic_id: 10 }, dependent: { concept_id: 5, topic_id: 11 } },
      { id: 2, status: "pending", rank: 3, prerequisite: { concept_id: 2, topic_id: 10 }, dependent: { concept_id: 6, topic_id: 11 } },
      { id: 3, status: "pending", rank: 1, prerequisite: { concept_id: 1, topic_id: 10 }, dependent: { concept_id: 6, topic_id: 11 } },
    ],
  }],
};

describe("learningOrder", () => {
  it("lists topics with content in outline order", () => {
    expect(learningOrder(ORDER_PATH).map((entry) => entry.topic.id)).toEqual([10, 11]);
  });

  it("puts confirmed links under 'may revisit' and pending ones, by rank, under 'might build on'", () => {
    const mixtures = learningOrder(ORDER_PATH)[1];
    const [solutions, air] = mixtures.steps;
    expect(solutions.revisit.map((link) => link.id)).toEqual([1]);
    expect(air.suggestions.map((link) => link.id)).toEqual([3, 2]);
    expect(mixtures.published).toBe(false);
  });
});
