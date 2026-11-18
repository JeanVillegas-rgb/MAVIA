import { describe, expect, it } from "vitest";

import { contentGaps, isShortOfQuestions } from "./TopicDetailPage";

// The server sends each concept its counts toward the minimum and the minimum
// itself; the page only compares them.
const concept = (counts, minimum = { LOT: 4, HOT: 2 }) => ({
  versions: { representative_id: 1, slots: { simplified: {}, elaborated: {} } },
  question_counts: counts,
  question_minimum: minimum,
});

describe("question minimum per concept", () => {
  it("is ready only when every concept meets the server's minimum", () => {
    expect(contentGaps([concept({ LOT: 4, HOT: 2 })]).ready).toBe(true);
    const short = contentGaps([concept({ LOT: 4, HOT: 2 }), concept({ LOT: 9, HOT: 1 })]);
    expect(short.shortQuestions).toBe(1);
    expect(short.ready).toBe(false);
  });

  it("follows whatever minimum the server sets", () => {
    expect(isShortOfQuestions(concept({ LOT: 1, HOT: 1 }, { LOT: 1, HOT: 1 }))).toBe(false);
    expect(isShortOfQuestions(concept({ LOT: 5, HOT: 5 }, { LOT: 6, HOT: 0 }))).toBe(true);
  });

  it("names the minimum in its messages", () => {
    expect(contentGaps([concept({ LOT: 0, HOT: 0 }, { LOT: 3, HOT: 1 })]).minimumText).toBe("3 LOTS and 1 HOTS");
  });
});
