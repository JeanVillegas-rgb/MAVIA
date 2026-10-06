import { describe, expect, it } from "vitest";

import { publishedClips } from "./publishedAudio";

const segment = (text, audio_url) => ({ text, audio_url });
const published = {
  topic: { id: 7, title: "Solid, Liquid and Gas" },
  steps: [
    {
      position: 2, title: "Solid",
      versions: {
        standard: { segments: [segment("Solids keep shape.", "/media/a.mp3"), segment("Ice is solid.", "/media/b.mp3")] },
        simplified: { segments: [segment("Easy.", "/media/s.mp3")] },
        elaborated: null,
      },
      questions: [{ id: 1, text: "Q?" }],
    },
    {
      position: 1, title: "Matter",
      versions: { standard: { segments: [segment("Matter is stuff.", "/media/m.mp3")] }, simplified: null, elaborated: null },
      questions: [],
    },
    {
      position: 3, title: "Gas",
      versions: { standard: { segments: [segment("Gas spreads.", ""), segment("Air.", "/media/g.mp3")] }, simplified: null, elaborated: null },
      questions: [],
    },
  ],
};

describe("publishedClips", () => {
  it("lists concepts in path order with only their Standard clips", () => {
    const rows = publishedClips(published);
    expect(rows.map((row) => row.title)).toEqual(["Matter", "Solid", "Gas"]);
    expect(rows[1].clips).toEqual(["/media/a.mp3", "/media/b.mp3"]);
  });

  it("counts the parts that have no audio", () => {
    const gas = publishedClips(published)[2];
    expect(gas.clips).toEqual(["/media/g.mp3"]);
    expect(gas.missing).toBe(1);
  });

  it("is empty when nothing was published", () => {
    expect(publishedClips(null)).toEqual([]);
  });

  it("gives each learning object its own named clip", () => {
    const step = {
      position: 1, title: "Comparing the Three States",
      versions: { standard: { segments: [
        { text: "a", audio_url: "/media/1.mp3", title: "Shape" },
        { text: "b", audio_url: "/media/2.mp3", title: "Volume" },
      ] } },
    };
    expect(publishedClips({ steps: [step] })[0].parts).toEqual([
      { url: "/media/1.mp3", title: "Shape" },
      { url: "/media/2.mp3", title: "Volume" },
    ]);
  });
});
