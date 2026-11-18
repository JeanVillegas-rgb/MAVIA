import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MaterialCard } from "./TopicDetailPage";

const material = {
  id: 1,
  title: "Matter lesson",
  filename: "matter.pdf",
  status: "completed",
  questions: [],
  generated_json: {
    learning_objects_confirmed: true,
    lesson_audio_generated: true,
    lesson_playlist: [{ title: "Matter narration", audio_url: "/media/lesson.mp3" }],
  },
  learning_objects: [{
    id: 10, kind: "text", title: "Matter", section_title: "Matter",
    content: "Matter has mass and occupies space.", order: 0,
  }],
};

describe("lesson-file content review", () => {
  it("shows learning objects directly without the audio tab or playlist", () => {
    const html = renderToStaticMarkup(<MaterialCard material={material} courseId={1} />);
    expect(html).toContain("Learning Objects");
    expect(html).toContain("Matter has mass and occupies space.");
    expect(html).not.toContain("material-workspace-tabs");
    expect(html).not.toContain("Lesson Playlist");
    expect(html).not.toContain("Generate lesson audio");
    expect(html).not.toContain("<audio");
  });

  it("keeps the missing-image-description notice in content review", () => {
    const imageMaterial = {
      ...material,
      learning_objects: [{ id: 11, kind: "image", title: "Diagram", content: "",
                           image_url: "/media/diagram.png", narration_pending: true }],
    };
    const html = renderToStaticMarkup(<MaterialCard material={imageMaterial} courseId={1} />);
    expect(html).toContain("no description");
    expect(html).toContain("/media/diagram.png");
    expect(html).not.toContain("<audio");
  });
});
