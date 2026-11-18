import { afterEach, describe, expect, it, vi } from "vitest";
import { reviewMaterialBlock } from "../api";

afterEach(() => vi.unstubAllGlobals());

describe("block review request", () => {
  it("posts only the source block id and teacher category to the correct material", async () => {
    vi.stubGlobal("localStorage", { getItem: () => "test-token" });
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 2 }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    expect(await reviewMaterialBlock(2, 13, 11, "lesson_content")).toEqual({ id: 2 });
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe("/api/courses/2/materials/13/review-block/");
    expect(options.method).toBe("POST");
    expect(options.headers.get("Authorization")).toBe("Token test-token");
    expect(JSON.parse(options.body)).toEqual({ block_id: 11, category: "lesson_content" });
  });

  it("surfaces locked-source errors instead of claiming the review was saved", async () => {
    vi.stubGlobal("localStorage", { getItem: () => null });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "Learning objects are confirmed" }), { status: 409 },
    )));
    await expect(reviewMaterialBlock(2, 13, 11, "other")).rejects.toThrow("Learning objects are confirmed");
  });
});
