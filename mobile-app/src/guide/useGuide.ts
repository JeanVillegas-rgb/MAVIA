// Plays the spoken guide, one part at a time, and remembers whether a learner
// has heard it.
//
// Parts are spoken in sequence rather than as one utterance so that stopping
// is instant: expo-speech only interrupts between utterances reliably, and a
// learner who presses a key three sentences in should not have to wait out the
// remaining two minutes. Each part hands on to the next through onDone, with
// useNarration's own timeout fallback as the safety net if a platform's TTS
// never calls back (see src/hooks/useNarration.ts).

import { useCallback, useEffect, useRef, useState } from "react";
import AsyncStorage from "@react-native-async-storage/async-storage";

import { FIRST_RUN_PARTS, guideSection } from "./script";

// Bumped when the guide's content changes enough that someone who heard the
// old one should hear the new one. A plain boolean would leave every existing
// learner on a guide that no longer describes the app.
const SEEN_KEY = "mavia.guide.heard.v1";

export async function hasHeardGuide(): Promise<boolean> {
  if (heardThisSession) return true;
  try {
    return (await AsyncStorage.getItem(SEEN_KEY)) === "yes";
  } catch {
    // Storage unreadable: say they have NOT heard it, and offer the practice.
    //
    // This used to answer "heard", to stop a broken store replaying the guide
    // on every launch. That is the wrong way round for the one learner this
    // is built for: getting the guide twice is a nuisance, never getting it is
    // an app they cannot work out how to use. A wrong "heard" is also silent
    // and permanent, where a wrong "not heard" they can end with the E key.
    return false;
  }
}

// Remembered in memory as well as on disk. If the write fails, or the read
// later does, a learner at least does not get the guide twice in one sitting.
let heardThisSession = false;

export async function markGuideHeard(): Promise<void> {
  heardThisSession = true;
  try {
    await AsyncStorage.setItem(SEEN_KEY, "yes");
  } catch {
    // Nothing to do -- worst case the guide offers itself again next launch.
  }
}

/** Forget that the guide was heard, so it plays again on next launch. */
export async function resetGuideHeard(): Promise<void> {
  try {
    await AsyncStorage.removeItem(SEEN_KEY);
  } catch {
    // Ignored, as above.
  }
}

const partKey = (id: string) => `mavia.guide.part.${id}.v1`;

/** A single guide part, offered once and then never again.
 *
 *  Used for the tip a learner needs exactly when they first arrive somewhere
 *  -- how to answer, at their first question -- rather than in a guide played
 *  minutes earlier and since forgotten.
 *
 *  `take()` returns the text the first time and an empty string afterwards, so
 *  a caller can splice it into whatever it was about to say. That matters: two
 *  separate utterances race, because expo-speech's `speak` stops whatever is
 *  already talking -- a tip spoken alongside a question would cut one of them
 *  off. One string, one utterance, no race.
 *
 *  `ready` is false until storage has answered. Callers wait for it before
 *  speaking, or the first question gets read before we know whether the tip
 *  belongs in front of it. */
export function useOneTimeGuidePart(id: string): { ready: boolean; take: () => string } {
  const [ready, setReady] = useState(false);
  const pendingRef = useRef("");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let heard = true;
      try {
        // Having heard the whole guide counts: the part was in it. Otherwise a
        // learner who just sat through the guide gets the answering tip again
        // thirty seconds later.
        const [wholeGuide, thisPart] = await Promise.all([hasHeardGuide(), AsyncStorage.getItem(partKey(id))]);
        heard = wholeGuide || thisPart === "yes";
      } catch {
        heard = true; // Storage trouble must never make it repeat forever.
      }
      if (cancelled) return;
      pendingRef.current = heard ? "" : guideSection(id)?.text ?? "";
      setReady(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  const take = useCallback(() => {
    const text = pendingRef.current;
    if (!text) return "";
    pendingRef.current = "";
    AsyncStorage.setItem(partKey(id), "yes").catch(() => {});
    return text;
  }, [id]);

  return { ready, take };
}

type Narrator = {
  speak: (text: string, options?: { onDone?: () => void }) => void;
  stop: () => void;
};

export type GuideController = {
  /** Play the whole guide from the top. Safe to call while already playing. */
  play: () => void;
  /** Play one section on its own -- what the minus-key menu hands back. */
  /** `onDone` runs when the section has finished being read, and not at
   *  all if the guide was stopped first -- the menu uses it to follow a
   *  section with the drills that rehearse it. */
  playSection: (id: string, options?: { onDone?: () => void }) => void;
  /** Stop immediately, wherever it is. */
  stop: () => void;
  playing: boolean;
};

export function useGuide(narration: Narrator): GuideController {
  const [playing, setPlaying] = useState(false);

  // The run counter is what makes stopping reliable. A part already handed to
  // the TTS engine will still fire its onDone after stop() -- that callback
  // checks the counter it captured, sees the run has moved on, and does
  // nothing, so a stopped guide can never resume itself one part later.
  const runRef = useRef(0);
  const narrationRef = useRef(narration);
  narrationRef.current = narration;

  const stop = useCallback(() => {
    runRef.current += 1;
    narrationRef.current.stop();
    setPlaying(false);
  }, []);

  const play = useCallback(() => {
    runRef.current += 1;
    const run = runRef.current;
    setPlaying(true);

    const speakFrom = (index: number) => {
      if (run !== runRef.current) return;
      const text = FIRST_RUN_PARTS[index];
      if (!text) {
        setPlaying(false);
        void markGuideHeard();
        return;
      }
      narrationRef.current.speak(text, { onDone: () => speakFrom(index + 1) });
    };

    speakFrom(0);
  }, []);

  // One section, for the replay menu. Marks the guide heard too: a learner
  // who is picking sections by letter has plainly already been through it.
  const playSection = useCallback((id: string, options?: { onDone?: () => void }) => {
    const section = guideSection(id);
    if (!section) return;
    runRef.current += 1;
    const run = runRef.current;
    setPlaying(true);
    narrationRef.current.speak(section.text, {
      onDone: () => {
        if (run !== runRef.current) return;
        setPlaying(false);
        options?.onDone?.();
      },
    });
    void markGuideHeard();
  }, []);

  // A learner who leaves the screen mid-guide should not keep hearing it.
  useEffect(() => stop, [stop]);

  return { play, playSection, stop, playing };
}

/** Plays the guide once, the first time a learner ever opens the app.
 *
 *  Returns the same controller, so the caller can also bind it to the minus
 *  key and to the "how does this work" voice command. */
export function useGuideOnFirstLaunch(
  narration: Narrator,
  // autoPlay false keeps the controller (minus key, sections) but leaves the
  // first-run behaviour to the caller -- the layout runs the practice drills
  // there instead of narrating the guide at someone.
  { enabled = true, autoPlay = true }: { enabled?: boolean; autoPlay?: boolean } = {}
): GuideController {
  const guide = useGuide(narration);
  const startedRef = useRef(false);
  const playRef = useRef(guide.play);
  playRef.current = guide.play;

  useEffect(() => {
    if (!enabled || !autoPlay || startedRef.current) return;
    startedRef.current = true;
    let cancelled = false;
    void hasHeardGuide().then((heard) => {
      if (!cancelled && !heard) playRef.current();
    });
    return () => {
      cancelled = true;
    };
  }, [enabled, autoPlay]);

  return guide;
}
