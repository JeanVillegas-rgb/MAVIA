// What the "Topic published" dialog plays: each concept's Normal telling, in
// path order, as the learner hears it. Questions and the Simplified and
// Elaborated versions are left out -- a learner meets those only on a miss.
export function publishedClips(published) {
  if (!published?.steps) return [];
  return [...published.steps]
    .sort((a, b) => a.position - b.position)
    .map((step) => {
      const segments = step.versions?.normal?.segments || [];
      const clips = segments.map((segment) => segment.audio_url).filter(Boolean);
      return {
        position: step.position,
        title: step.title,
        clips,
        missing: segments.length - clips.length,
      };
    });
}
