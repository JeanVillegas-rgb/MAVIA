// Answering by tapping, for when the braille keypad is not at hand: tap the
// question once for A, twice for B, three times for C, four times for D.
//
// Each tap is spoken as it lands ("A", "B", "C"), so the learner hears the
// count while tapping instead of trusting an unseen tally. The count becomes
// the answer once the taps stop for TAP_SETTLE_MS.

import { useCallback, useEffect, useRef } from "react";

import type { AnswerLetter } from "./brailleKeypad";

// How long after the last tap the count is taken as the answer. Long enough for
// a child's unhurried taps to still count as one run; short enough that the
// answer does not seem to hang after the final tap.
export const TAP_SETTLE_MS = 900;

// Same order as the keypad: 7 8 9 + and 1 2 3 4 taps both mean A B C D.
const LETTER_FOR_COUNT: AnswerLetter[] = ["a", "b", "c", "d"];

export function letterForTapCount(count: number): AnswerLetter | null {
  return LETTER_FOR_COUNT[count - 1] ?? null;
}

/** Counts a run of taps. Returns the function to call on each tap.
 *
 *  `onTap(count)` runs on every tap with the running count; `onSettled(count)`
 *  runs once the taps stop. Both are read through refs, so the caller can pass
 *  fresh callbacks every render. */
export function useTapCounter({
  onTap,
  onSettled,
  settleMs = TAP_SETTLE_MS,
}: {
  onTap: (count: number) => void;
  onSettled: (count: number) => void;
  settleMs?: number;
}): () => void {
  const countRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onTapRef = useRef(onTap);
  const onSettledRef = useRef(onSettled);
  onTapRef.current = onTap;
  onSettledRef.current = onSettled;

  // A card that unmounts mid-count (answered by keypad, left the screen) must
  // not submit a stale count afterwards.
  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    []
  );

  return useCallback(() => {
    countRef.current += 1;
    onTapRef.current(countRef.current);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      const count = countRef.current;
      countRef.current = 0;
      timerRef.current = null;
      onSettledRef.current(count);
    }, settleMs);
  }, [settleMs]);
}
