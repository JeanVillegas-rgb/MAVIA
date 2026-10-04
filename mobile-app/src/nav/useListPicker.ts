// Choosing from a list by ear: read four out, take a letter, page on request.
//
// Four is the page size because four is how many answer keys there are, so the
// same finger positions that answer a question also pick a course. A learner
// never has to hold more than four things in mind, and never learns a second
// set of keys.
//
// The one subtlety worth knowing about is why letters are ignored while this
// is speaking. The microphone hears the phone's own speaker, so reading "A,
// Science. B, Maths." out loud puts the words "a" and "b" straight into the
// recognizer -- and a bare letter is a valid command here. Without the guard
// the list would pick its own first option every time it opened. Letters are
// therefore only accepted once the prompt has finished and a short settle has
// passed, which is also roughly when a learner could first have answered.

import { useCallback, useEffect, useRef, useState } from "react";
import { useFocusEffect } from "expo-router";

import { useBrailleKeypad } from "@/input/useBrailleKeypad";
import { useVoiceCommands } from "@/voice/useVoiceCommands";
import type { AnswerLetter } from "@/input/brailleKeypad";
import { COMMAND_KEYS } from "@/guide/script";

export const PAGE_SIZE = 4;
const LETTERS: AnswerLetter[] = ["a", "b", "c", "d"];

// How long after the prompt stops before a spoken letter counts. Long enough
// for the tail of the recognizer's own reading of the options to land and be
// discarded; short enough that a learner answering promptly is still heard.
const ACCEPT_DELAY_MS = 600;

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
  enabled = true,
}: {
  items: T[];
  /** What to read aloud for an item. */
  labelOf: (item: T) => string;
  /** e.g. "Which course would you like?" -- spoken before the four options. */
  question: string;
  narration: Narrator;
  onPick: (item: T) => void;
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
  const acceptingRef = useRef(false);
  const settleRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const questionRef = useRef(question);
  questionRef.current = question;
  const labelOfRef = useRef(labelOf);
  labelOfRef.current = labelOf;
  const pageCountRef = useRef(pageCount);
  pageCountRef.current = pageCount;

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
        ? ` If the one you want is not there, say next four, or press the ${COMMAND_KEYS.next} key.`
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
    if (!acceptingRef.current) return;
    const item = pageRef.current[LETTERS.indexOf(letter)];
    if (!item) {
      narrationRef.current.speak(`There is no option ${letter.toUpperCase()} here.`);
      return;
    }
    acceptingRef.current = false;
    onPickRef.current(item);
  }, []);

  // Read the page whenever it changes, and when the list first arrives.
  const signature = `${safePage}:${page.map((item) => item.id).join(",")}`;
  useEffect(() => {
    if (!live) return;
    readPage();
    return () => {
      if (settleRef.current) clearTimeout(settleRef.current);
      acceptingRef.current = false;
      // Silence on the way out. Without this the prompt carried on over
      // whatever screen came next -- speak() only cancels the PREVIOUS
      // utterance, so a picker that is merely unmounted keeps talking.
      narrationRef.current.stop();
    };
  }, [live, signature, readPage]);

  useVoiceCommands(
    {
      chooseA: () => pick("a"),
      chooseB: () => pick("b"),
      chooseC: () => pick("c"),
      chooseD: () => pick("d"),
      nextPage: () => nextPage(),
      repeatQuestion: () => readPage(),
      repeatTopic: () => readPage(),
    },
    { enabled: live }
  );

  useBrailleKeypad(
    (action) => {
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
