// What the "Topic published" dialog plays: each concept's Standard telling, in
// path order, as the learner hears it. Questions and the Simplified and
// Elaborated versions are left out -- a learner meets those only on a miss.
export function publishedClips(published) {
  if (!published?.steps) return [];
  return [...published.steps]
    .sort((a, b) => a.position - b.position)
    .map((step) => {
      const segments = step.versions?.standard?.segments || [];
      // One playable clip per learning object of the Standard version, each
      // named for the object it speaks for.
      const parts = segments
        .filter((segment) => segment.audio_url)
        .map((segment) => ({ url: segment.audio_url, title: segment.title || "" }));
      return {
        position: step.position,
        title: step.title,
        clips: parts.map((part) => part.url),
        parts,
        missing: segments.length - parts.length,
      };
    });
}
