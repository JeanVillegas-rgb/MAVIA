// One switch that silences everything that can make a sound.
//
// The app has two independent sound sources -- expo-speech for narration and
// expo-audio for lesson tracks -- and several live instances of each, one per
// screen plus the layout's. Stopping "the audio" therefore meant remembering
// every one of them from wherever you happened to be, and the back key, which
// lives in the layout, could not reach a screen's player at all. Every fix
// that worked through blur timing or through one hook's own stop() left some
// path uncovered, and the symptom was always the same: two voices at once
// after going back.
//
// So each source registers itself here when it mounts, and anything that ends
// a screen calls silenceAll(). Nothing has to know what else exists.

type Silencer = () => void;

const silencers = new Set<Silencer>();

/** Register a stop function. Returns the unregister, for effect cleanup. */
export function registerSilencer(stop: Silencer): () => void {
  silencers.add(stop);
  return () => {
    silencers.delete(stop);
  };
}

/** Stop every registered sound source, now.
 *
 *  Deliberately forgiving: one source throwing (a player already torn down,
 *  say) must not leave the rest of them talking. */
export function silenceAll(): void {
  silencers.forEach((stop) => {
    try {
      stop();
    } catch {
      // Already gone. The point is that everything else still gets stopped.
    }
  });
}
