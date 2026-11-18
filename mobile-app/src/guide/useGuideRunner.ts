// The spoken guide, on the guide key (.).
//
// Pressing it asks one question -- "A, the whole guide, or B, choose one part?"
// -- and plays what was chosen. Narration only: nothing is drilled, nothing
// listens for a voice, and nothing nests, because the old section menu running
// practice drills over the same keys as the screens underneath is what made
// the guide fail before.
//
// One state, one place:
//   closed   -> nothing; the screens below have the keys
//   asking   -> "A: whole guide, B: choose a part" (A / B answer it)
//   choosing -> the four parts read out, A to D (the letter plays that part)
//   playing  -> narration running
// While it is anything but closed, the guide owns the answer keys and the
// screens below are told to keep quiet (GuideActivityProvider's `busy`).
// The guide key or the back key stops it from any state.
//
// Every run carries a token; a narration that finishes after the guide was
// stopped or restarted sees a stale token and does nothing, so a cut-off part
// can never fire a late "onDone" and jump the learner somewhere.

import { useCallback, useEffect, useRef, useState } from "react";

import type { AnswerLetter } from "@/input/brailleKeypad";
import { silenceAll } from "@/hooks/audioBus";
import { hasHeardGuide, markGuideHeard } from "./useGuide";
import {
  ANSWER_KEYS,
  FIRST_RUN_PARTS,
  GUIDE_ENTRY_QUESTION,
  GUIDE_MENU_QUESTION,
  GUIDE_SECTIONS,
} from "./script";

type Narrator = {
  speak: (text: string, options?: { onDone?: () => void }) => void;
  stop: () => void;
};

export type GuideMode = "closed" | "asking" | "choosing" | "playing";

const LETTERS: AnswerLetter[] = ["a", "b", "c", "d"];

const PART_MENU =
  `${GUIDE_MENU_QUESTION} ` +
  GUIDE_SECTIONS.map((section, index) => `${LETTERS[index].toUpperCase()}. ${section.title}.`).join(" ");

export type GuideRunner = {
  mode: GuideMode;
  /** True while the guide is open in any state: screens below keep quiet. */
  busy: boolean;
  /** The guide key: open the guide if closed, close it otherwise. */
  toggle: () => void;
  /** Close it, silencing whatever it was saying. */
  stop: () => void;
  /** An answer key, while the guide is open. Returns true if it was used. */
  answer: (letter: AnswerLetter) => boolean;
  /** The repeat key, while the guide is open: say the current prompt again. */
  repeat: () => void;
};

export function useGuideRunner(narration: Narrator, { signedIn }: { signedIn: boolean }): GuideRunner {
  const [mode, setMode] = useState<GuideMode>("closed");
  const modeRef = useRef<GuideMode>("closed");
  const runRef = useRef(0);
  const narrationRef = useRef(narration);
  narrationRef.current = narration;

  const enter = useCallback((next: GuideMode) => {
    modeRef.current = next;
    setMode(next);
  }, []);

  const stop = useCallback(() => {
    runRef.current += 1;
    narrationRef.current.stop();
    enter("closed");
  }, [enter]);

  // Speak a list of parts one after another, then close. A fresh token makes
  // any earlier run's late callbacks harmless.
  const playParts = useCallback(
    (parts: string[], onFinished?: () => void) => {
      const run = ++runRef.current;
      silenceAll();
      enter("playing");
      const next = (index: number) => {
        if (runRef.current !== run) return;
        if (index >= parts.length) {
          enter("closed");
          onFinished?.();
          return;
        }
        narrationRef.current.speak(parts[index], { onDone: () => next(index + 1) });
      };
      next(0);
    },
    [enter]
  );

  const ask = useCallback(
    (state: "asking" | "choosing") => {
      runRef.current += 1;
      silenceAll();
      enter(state);
      narrationRef.current.speak(state === "asking" ? GUIDE_ENTRY_QUESTION : PART_MENU);
    },
    [enter]
  );

  const playWhole = useCallback(() => {
    playParts(FIRST_RUN_PARTS, () => void markGuideHeard());
  }, [playParts]);

  const toggle = useCallback(() => {
    if (modeRef.current === "closed") ask("asking");
    else stop();
  }, [ask, stop]);

  const answer = useCallback(
    (letter: AnswerLetter): boolean => {
      const current = modeRef.current;
      if (current === "asking") {
        if (letter === "a") playWhole();
        else if (letter === "b") ask("choosing");
        else narrationRef.current.speak(`Press ${ANSWER_KEYS.a} for the whole guide, or ${ANSWER_KEYS.b} to choose one part.`);
        return true;
      }
      if (current === "choosing") {
        const section = GUIDE_SECTIONS[LETTERS.indexOf(letter)];
        if (section) playParts([`${section.title}. ${section.text}`]);
        return true;
      }
      // While a part is playing, the answer keys do nothing -- they must not
      // reach the screen underneath either.
      return current === "playing";
    },
    [ask, playParts, playWhole]
  );

  const repeat = useCallback(() => {
    const current = modeRef.current;
    if (current === "asking" || current === "choosing") ask(current);
  }, [ask]);

  // The first time a student ever signs in, the whole guide plays by itself.
  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    void hasHeardGuide().then((heard) => {
      if (!cancelled && !heard && modeRef.current === "closed") playWhole();
    });
    return () => {
      cancelled = true;
    };
    // Only on the transition into being signed in.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signedIn]);

  return { mode, busy: mode !== "closed", toggle, stop, answer, repeat };
}
