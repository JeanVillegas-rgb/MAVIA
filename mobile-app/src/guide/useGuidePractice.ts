// Runs the practice drills: say one, wait for the learner to do it, confirm,
// go on.
//
// The whole point is the waiting, so this owns the learner's input while it
// runs. `feed` returns true when a drill consumed something, and the screen
// that called it must then do nothing else with that press -- otherwise the
// divide drill would also navigate away, and the letter drill would answer a
// real question. Everything not being waited for is swallowed too: a learner
// pressing keys to find the right one should not set anything else off.
//
// Nothing here can strand anyone. A drill that goes unanswered nudges, then
// nudges once more, then gives up and moves on -- being stuck on step one
// with no way past is a worse failure than missing a drill.

import { useCallback, useEffect, useRef, useState } from "react";

import type { AnswerLetter } from "@/input/brailleKeypad";
import {
  DRILLS,
  drillsForSection,
  MAX_NUDGES,
  NUDGE_AFTER_MS,
  PRACTICE_CLOSING,
  PRACTICE_SKIP,
  PRACTICE_WELCOME,
  type Drill,
  type ExpectedInput,
  type InputSource,
} from "./practice";
import type { GuideSectionId } from "./script";
import { markGuideHeard } from "./useGuide";

export type PracticeInput =
  | { kind: "swipe" }
  | { kind: "command"; command: "repeat" | "back" | "next" }
  | { kind: "letter"; letter: AnswerLetter; via: InputSource };

// Roughly how long a prompt takes to read, used only as a floor for the
// watchdog below -- the same ~11 chars/sec useNarration estimates with, plus
// room so the watchdog never fires while the voice is still going.
function promptTimeoutMs(text: string): number {
  return Math.min(Math.max((text.length / 11) * 1000, 3000), 30000) + 2000;
}

// Practice is the one flow whose behaviour is timing a learner cannot see and
// a log cannot infer: a nudge and a success sound alike from outside, and a
// drill that silently never started waiting looks exactly like one that was
// answered. These make a run readable in `adb logcat -s ReactNativeJS`.
// Dev only -- a released build needs none of it.
function trace(...parts: unknown[]) {
  if (__DEV__) console.log("[practice]", ...parts);
}

function matches(expected: ExpectedInput, got: PracticeInput): boolean {
  if (expected.kind !== got.kind) return false;
  if (expected.kind === "letter" && got.kind === "letter") {
    if (expected.letter !== got.letter) return false;
    // A drill teaching one way of answering only accepts that way; one with no
    // `via` takes the letter however it arrives.
    return expected.via === undefined || expected.via === got.via;
  }
  if (expected.kind === "command" && got.kind === "command") return expected.command === got.command;
  return true;
}

type Narrator = {
  speak: (text: string, options?: { onDone?: () => void }) => void;
  stop: () => void;
};

export type Practice = {
  running: boolean;
  /** True only while a drill is waiting to be answered by tapping. The screen
   *  puts a tap-catching layer up for exactly that long -- any longer and it
   *  would swallow the swipe drill and every real control underneath. */
  awaitingTap: boolean;
  /** Hand it an input. Returns true if practice used it -- the caller must
   *  then not act on that input itself. */
  feed: (input: PracticeInput) => boolean;
  /** With a section, rehearse only the moves that section just described;
   *  without one, run the whole first-run set. */
  start: (section?: GuideSectionId) => void;
  /** Abandon the run. The guide still counts as heard: they sat through it. */
  quit: () => void;
};

export function useGuidePractice(narration: Narrator): Practice {
  const [running, setRunning] = useState(false);
  const [awaitingTap, setAwaitingTap] = useState(false);

  // Same guard as useGuide: a run counter, so a speech callback that fires
  // after the run ended (or was restarted) does nothing instead of advancing
  // a drill that is no longer on screen.
  // The drills this run walks: one section's, or the whole first-run set.
  const queueRef = useRef<Drill[]>(DRILLS);
  const runRef = useRef(0);
  const stepRef = useRef(0);
  const nudgesRef = useRef(0);
  // Only true between "the prompt has finished" and "they got it right".
  // Input arriving while the prompt is still being read is ignored rather
  // than counted, because the microphone hears the prompt's own letters.
  const waitingRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const narrationRef = useRef(narration);
  narrationRef.current = narration;

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const finish = useCallback(() => {
    runRef.current += 1;
    clearTimer();
    waitingRef.current = false;
    setAwaitingTap(false);
    setRunning(false);
  }, [clearTimer]);

  // Declared as a ref so runStep and feed can call each other without either
  // being defined first.
  const runStepRef = useRef<(index: number) => void>(() => {});

  const armNudge = useCallback(
    (run: number, index: number) => {
      clearTimer();
      timerRef.current = setTimeout(() => {
        if (run !== runRef.current || !waitingRef.current) return;
        const drill = queueRef.current[index];
        if (!drill) return;
        nudgesRef.current += 1;
        if (nudgesRef.current > MAX_NUDGES) {
          trace("giving up on", drill.id, "- moving on");
          waitingRef.current = false;
          narrationRef.current.speak(PRACTICE_SKIP, {
            onDone: () => {
              if (run !== runRef.current) return;
              runStepRef.current(index + 1);
            },
          });
          return;
        }
        trace("nudge", drill.id, nudgesRef.current, "of", MAX_NUDGES);
        narrationRef.current.speak(drill.nudge, {
          onDone: () => {
            if (run !== runRef.current) return;
            armNudge(run, index);
          },
        });
      }, NUDGE_AFTER_MS);
    },
    [clearTimer]
  );

  const runStep = useCallback(
    (index: number) => {
      const run = runRef.current;
      stepRef.current = index;
      nudgesRef.current = 0;
      waitingRef.current = false;
      setAwaitingTap(false);
      clearTimer();

      const drill = queueRef.current[index];
      if (!drill) {
        trace("all drills done - closing");
        narrationRef.current.speak(PRACTICE_CLOSING, {
          onDone: () => {
            if (run !== runRef.current) return;
            finish();
          },
        });
        void markGuideHeard();
        return;
      }

      const beginWaiting = () => {
        if (run !== runRef.current || waitingRef.current) return;
        waitingRef.current = true;
        setAwaitingTap(drill.expects.kind === "letter" && drill.expects.via === "tap");
        trace("waiting for", drill.id, JSON.stringify(drill.expects));
        armNudge(run, index);
      };
      trace("drill", index + 1, "of", queueRef.current.length, ":", drill.id);
      narrationRef.current.speak(drill.prompt, { onDone: beginWaiting });
      // Watchdog. onDone is not guaranteed -- a platform can drop it, and
      // anything else calling speak() cancels it outright -- and a drill that
      // never starts waiting can never be answered or nudged. Start listening
      // regardless once the prompt has had time to be read.
      clearTimer();
      timerRef.current = setTimeout(beginWaiting, promptTimeoutMs(drill.prompt));
    },
    [armNudge, clearTimer, finish]
  );
  runStepRef.current = runStep;

  const start = useCallback((section?: GuideSectionId) => {
    queueRef.current = section ? drillsForSection(section) : DRILLS;
    runRef.current += 1;
    const run = runRef.current;
    setRunning(true);
    nudgesRef.current = 0;
    trace("run started");
    narrationRef.current.speak(PRACTICE_WELCOME, {
      onDone: () => {
        if (run !== runRef.current) return;
        runStepRef.current(0);
      },
    });
  }, []);

  const quit = useCallback(() => {
    narrationRef.current.stop();
    void markGuideHeard();
    finish();
  }, [finish]);

  // feed is called from key handlers bound once, so it must not go stale.
  const runningRef = useRef(running);
  runningRef.current = running;

  const feed = useCallback(
    (input: PracticeInput) => {
      if (!runningRef.current) return false;
      trace("input", JSON.stringify(input), "waiting=" + waitingRef.current);
      // Only a run that is actually waiting for an answer consumes input.
      //
      // This used to swallow everything while running, on the reasoning that a
      // stray press should not reach the app behind the practice. That was
      // wrong in the one case that mattered: if a prompt's onDone never fired
      // -- which happens when anything else calls speak() and cancels it --
      // the run stayed forever "running but not waiting" and ate every key the
      // learner pressed, the back key included. A dead back key is far worse
      // than a stray press, so input now falls through to the app instead.
      if (!waitingRef.current) return false;

      const drill = queueRef.current[stepRef.current];
      if (!drill) return true;
      if (!matches(drill.expects, input)) {
        trace("wrong input for", drill.id, "- ignored");
        return true;
      }

      const run = runRef.current;
      waitingRef.current = false;
      setAwaitingTap(false);
      clearTimer();
      trace("PASSED", drill.id);
      const goOn = () => {
        if (run !== runRef.current) return;
        runStepRef.current(stepRef.current + 1);
      };
      narrationRef.current.speak(drill.success, { onDone: goOn });
      // Watchdog, for the same reason the prompt has one: if anything cancels
      // this utterance its onDone never arrives, and the run would stop dead
      // on a drill the learner has already passed.
      clearTimer();
      timerRef.current = setTimeout(goOn, promptTimeoutMs(drill.success));
      return true;
    },
    [clearTimer]
  );

  useEffect(() => () => {
    runRef.current += 1;
    clearTimer();
  }, [clearTimer]);

  return { running, awaitingTap, feed, start, quit };
}
