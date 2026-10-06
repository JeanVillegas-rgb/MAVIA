// Choosing from a list by ear: read four out, take a letter, page on request.
//
// Four is the page size because four is how many answer keys there are, so the
// same finger positions that answer a question also pick a course. A learner
// never has to hold more than four things in mind, and never learns a second
// set of keys.
//
// Chosen with the keypad's answer keys (voice commands are switched off for
// now -- not reliable enough). Every choice is read back before it opens --
// "A. Opening course: Grade 1 Science" -- so a learner who cannot see the
// screen hears which key registered and what is about to happen.

import { useCallback, useEffect, useRef, useState } from "react";
import { useFocusEffect } from "expo-router";

import { useBrailleKeypad } from "@/input/useBrailleKeypad";
import type { AnswerLetter } from "@/input/brailleKeypad";
import { COMMAND_KEYS } from "@/guide/script";

export const PAGE_SIZE = 4;
const LETTERS: AnswerLetter[] = ["a", "b", "c", "d"];

// How long after the prompt stops before a choice counts (a key press is
// always taken at once -- see the keypad handler below).
const ACCEPT_DELAY_MS = 600;
// The read-back opens the choice when it finishes; this is the longest it may
// take, so a voice that is cut off can never leave the learner stuck.
const READ_BACK_MAX_MS = 5000;

type Narrator = {
  speak: (text: string, options?: { onDone?: () => void }) => void;
  stop: () => void;
};

// Only an id is required. What to read out is a function the caller passes,
// so a model keeps its own field names -- a Course has a `title`, a Lesson may
// not, and neither should have to grow a `label` to be pickable.
export type PickerItem = { id: string | number };

export type ListPicker<T> = {
  /** The items currently being offered, at most PAGE_SIZE of them. */
  page: T[];
  /** 0-based page number, and how many pages there are in total. */
  pageIndex: number;
  pageCount: number;
  /** Read the current page out again from the top. */
  readPage: () => void;
  /** Move to the next page (wraps back to the first) and read it. */
  nextPage: () => void;
  /** The letter each item on this page answers to, same order as `page`. */
  lettersForPage: AnswerLetter[];
};

export function useListPicker<T extends PickerItem>({
  items,
  labelOf,
  question,
  narration,
  onPick,
  readBack,
  enabled = true,
}: {
  items: T[];
  /** What to read aloud for an item. */
  labelOf: (item: T) => string;
  /** e.g. "Which course would you like?" -- spoken before the four options. */
  question: string;
  narration: Narrator;
  onPick: (item: T) => void;
  /** What to say once an item is chosen, before it opens. Default:
   *  "A. <label>". e.g. (letter, c) => `${letter}. Opening course: ${c.title}` */
  readBack?: (letter: string, item: T) => string;
  enabled?: boolean;
}): ListPicker<T> {
  const [pageIndex, setPageIndex] = useState(0);

  // A screen that has been navigated away from stays mounted in the router
  // stack, and its picker stayed live with it: the course list would read its
  // prompt again over the lesson list that replaced it, which is the doubled,
  // overlapping audio. Only the screen actually in front of the learner
  // speaks.
  const [focused, setFocused] = useState(true);
  useFocusEffect(
    useCallback(() => {
      setFocused(true);
      return () => setFocused(false);
    }, [])
  );
  const live = enabled && focused;
  const pageCount = Math.max(1, Math.ceil(items.length / PAGE_SIZE));

  // Clamp rather than reset: a list that loads in stages (or shrinks) should
  // not throw a learner back to the first page mid-sentence.
  const safePage = Math.min(pageIndex, pageCount - 1);
  const page = items.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE);
  const lettersForPage = LETTERS.slice(0, page.length);

  // Read through refs so the handlers below never need re-binding, which would
  // tear down the microphone session on every render.
  const pageRef = useRef(page);
  pageRef.current = page;
  const onPickRef = useRef(onPick);
  onPickRef.current = onPick;
  const narrationRef = useRef(narration);
  narrationRef.current = narration;

  // Set while the prompt is being spoken, and for a moment after. See the note
  // at the top of this file: this is what stops the list picking itself.
  // Set while handing over to whatever the pick starts, so the unmount
  // cleanup below knows not to silence it.
  const pickedRef = useRef(false);
  const acceptingRef = useRef(false);
  const settleRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const questionRef = useRef(question);
  questionRef.current = question;
  const labelOfRef = useRef(labelOf);
  labelOfRef.current = labelOf;
  const pageCountRef = useRef(pageCount);
  pageCountRef.current = pageCount;
  const readBackRef = useRef(readBack);
  readBackRef.current = readBack;

  const readPage = useCallback(() => {
    const current = pageRef.current;
    if (current.length === 0) return;

    acceptingRef.current = false;
    if (settleRef.current) clearTimeout(settleRef.current);

    const options = current
      .map((item, index) => `${LETTERS[index].toUpperCase()}. ${labelOfRef.current(item)}.`)
      .join(" ");
    const more =
      pageCountRef.current > 1
        ? ` If the one you want is not there, press the ${COMMAND_KEYS.next} key for the next four.`
        : "";

    narrationRef.current.speak(`${questionRef.current} ${options}${more}`, {
      onDone: () => {
        settleRef.current = setTimeout(() => {
          acceptingRef.current = true;
        }, ACCEPT_DELAY_MS);
      },
    });
  }, []);

  const nextPage = useCallback(() => {
    setPageIndex((current) => (current + 1) % Math.max(1, pageCountRef.current));
  }, []);

  const pick = useCallback((letter: AnswerLetter) => {
    if (!acceptingRef.current) {
      // A letter arriving while the prompt is still being read is dropped on
      // purpose (the microphone hears the phone's own speaker). Saying so
      // tells a dropped press apart from a picker that never got the key.
      if (__DEV__) console.log(`[MAVIA picker] ignored "${letter}": still reading the options`);
      return;
    }
    const item = pageRef.current[LETTERS.indexOf(letter)];
    if (!item) {
      narrationRef.current.speak(`There is no option ${letter.toUpperCase()} here.`);
      return;
    }
    acceptingRef.current = false;
    pickedRef.current = true;

    // Say the choice, then open it -- once, whichever comes first: the
    // read-back finishing, or the longest it could take.
    const upper = letter.toUpperCase();
    const label = labelOfRef.current(item);
    const line = readBackRef.current ? readBackRef.current(upper, item) : `${upper}. ${label}.`;
    let opened = false;
    const open = () => {
      if (opened) return;
      opened = true;
      clearTimeout(fallback);
      onPickRef.current(item);
    };
    const fallback = setTimeout(open, READ_BACK_MAX_MS);
    narrationRef.current.speak(line, { onDone: open });
  }, []);

  // Read the page whenever it changes, and when the list first arrives.
  const signature = `${safePage}:${page.map((item) => item.id).join(",")}`;
  useEffect(() => {
    if (!live) return;
    pickedRef.current = false;
    readPage();
    return () => {
      if (settleRef.current) clearTimeout(settleRef.current);
      acceptingRef.current = false;
      // Silence on the way out. Without this the prompt carried on over
      // whatever screen came next -- speak() only cancels the PREVIOUS
      // utterance, so a picker that is merely unmounted keeps talking.
      //
      // Unless it was a pick that closed us. Choosing hands straight over to
      // something that speaks -- a guide section, then its drills -- and that
      // starts before this cleanup runs, so stopping here cut the section off
      // mid-word and its onDone never fired, which is what left the drills
      // never starting. A picker that handed over does not silence what it
      // handed to.
      if (!pickedRef.current) narrationRef.current.stop();
    };
  }, [live, signature, readPage]);

  const numLockWarnedRef = useRef(false);

  useBrailleKeypad(
    (action) => {
      if (__DEV__) console.log(`[MAVIA picker] key: ${action.kind}`);
      // With Num Lock off the answer keys send navigation codes, not digits,
      // so every letter press lands here and nowhere else. QuestionCard said
      // so; a list did not, which left the keys looking simply dead -- the
      // guide menu reads out four options and then ignores the key for each
      // one. Say it here too, once per page so a held arrow is not a loop.
      if (action.kind === "numLockOff") {
        if (numLockWarnedRef.current) return;
        numLockWarnedRef.current = true;
        narrationRef.current.speak(
          `Number lock is off. Press Num Lock, then choose with ` +
            `${LETTERS.map((letter) => letter.toUpperCase()).join(", ")}.`
        );
        return;
      }
      // A key press is deliberate in a way a heard word is not, so it is taken
      // even while the prompt is still being read: pressing a key is how an
      // impatient learner skips the reading.
      if (action.kind === "answer") {
        acceptingRef.current = true;
        pick(action.letter);
        return;
      }
      if (action.kind === "next") nextPage();
      if (action.kind === "repeat") readPage();
    },
    { enabled: live }
  );

  return { page, pageIndex: safePage, pageCount, readPage, nextPage, lettersForPage };
}
