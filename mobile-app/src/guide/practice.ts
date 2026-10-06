// The first run is a practice run, not a lecture.
//
// Hearing "swipe right to reach your courses" and having swiped right are
// different things, and only the second one is still true tomorrow. So each
// drill below teaches one move in a sentence, asks for it, and waits: nothing
// advances until the learner has actually done it. They get told they got it
// right, which is the part a blind learner otherwise has no way to know.
//
// The stage is deliberately fake. The four items and the practice question are
// hardcoded here, so a learner can get a letter wrong without it opening a
// real lesson, and so the drill reads the same on a phone with no courses on
// it at all -- including on a stage, being demonstrated.
//
// Keep every line short. This is the one part of the app a learner cannot skip
// past on their first visit, so it earns its length or it loses them.

import type { AnswerLetter } from "@/input/brailleKeypad";
import { ANSWER_KEYS, COMMAND_KEYS, type GuideSectionId } from "./script";

/** How an answer was given. A drill that teaches one way of answering has to
 *  be able to tell it apart from the others, or "tap three times for C" would
 *  be satisfied by pressing the C key and the learner would never have tapped. */
export type InputSource = "key" | "voice" | "tap";

/** What a drill is waiting for. Anything else is ignored while it waits.
 *  A letter drill with no `via` accepts the letter however it arrives. */
export type ExpectedInput =
  | { kind: "swipe" }
  | { kind: "command"; command: "repeat" | "back" | "next" }
  | { kind: "letter"; letter: AnswerLetter; via?: InputSource };

export type Drill = {
  id: string;
  /** The guide section this drill rehearses. Hearing a section and then doing
   *  the move it just described is the point: the words are the lesson, the
   *  drill is the practice. `GuideMenu` plays a section and then runs the
   *  drills tagged with it. */
  section: GuideSectionId;
  /** Teaches the move and asks for it, in one breath. */
  prompt: string;
  expects: ExpectedInput;
  /** Said the moment they get it right. */
  success: string;
  /** Said if they stall, shorter than the prompt -- a reminder, not a repeat. */
  nudge: string;
};

export const DRILLS: Drill[] = [
  {
    id: "swipe",
    section: "moving",
    prompt:
      "Let us try each one. First, finding a lesson. " +
      "Put a finger anywhere on the screen and slide it to the right. Go ahead.",
    expects: { kind: "swipe" },
    success: "That is it. A swipe to the right always takes you to your courses.",
    nudge: "Slide one finger across the screen, from left to right.",
  },
  {
    id: "letter",
    section: "choosing",
    prompt:
      "Now, choosing. I read four things out, each with a letter. " +
      "A, Science. B, Maths. C, English. D, History. " +
      `Choose Maths. Say it, or press the key marked ${ANSWER_KEYS.b}.`,
    expects: { kind: "letter", letter: "b" },
    success: "Correct, B was Maths. Courses and lessons are always chosen that way.",
    nudge: `Maths was the second one. Say ${ANSWER_KEYS.b}, or press that key.`,
  },
  {
    id: "repeat",
    section: "listening",
    prompt:
      "If anything is ever read too quickly, you can hear it again. " +
      `Press the ${COMMAND_KEYS.repeat} key now.`,
    expects: { kind: "command", command: "repeat" },
    success: `Good. The ${COMMAND_KEYS.repeat} key repeats whatever is playing, wherever you are.`,
    nudge: `Find the ${COMMAND_KEYS.repeat} key on your keypad and press it.`,
  },
  // Answering has three ways, and they get three drills. Read out together
  // they were a mouthful nobody could hold -- and a learner who only ever
  // pressed a key never found out that tapping works at all.
  {
    id: "answer-tap",
    section: "answering",
    prompt:
      "Now a question, the way your lessons will ask them. " +
      "Which of these is a solid? A, water. B, air. C, ice. D, steam. " +
      "Ice is the third one, so the answer is C. " +
      "First, let us try answering by tapping. Tap anywhere on the screen three times.",
    expects: { kind: "letter", letter: "c", via: "tap" },
    success: "Correct, three taps for C. I say each letter as you tap, so you can hear where you are.",
    nudge: "Tap the screen three times, one tap for each letter up to C.",
  },
  {
    id: "answer-key",
    section: "answering",
    prompt:
      "Now the same answer with a key. " +
      `Press the key marked ${ANSWER_KEYS.c}.`,
    expects: { kind: "letter", letter: "c", via: "key" },
    success:
      `Correct. The four keys are marked ${ANSWER_KEYS.a}, ${ANSWER_KEYS.b}, ${ANSWER_KEYS.c} and ${ANSWER_KEYS.d}. ` +
      "Questions are answered by key or by tapping -- saying the letter is for choosing a course or a lesson, not for answering.",
    nudge: `Find the key marked ${ANSWER_KEYS.c} and press it.`,
  },
  {
    id: "back",
    section: "moving",
    prompt:
      "Last one. To leave anything at all, go back. " +
      `Press the ${COMMAND_KEYS.back} key now.`,
    expects: { kind: "command", command: "back" },
    success: `That is everything. The ${COMMAND_KEYS.back} key gets you out of anywhere.`,
    nudge: `Find the ${COMMAND_KEYS.back} key on your keypad and press it.`,
  },
];

export const PRACTICE_WELCOME =
  "Welcome to MAVIA. Rather than tell you how this works, let us practise it. " +
  "There are five short things to try, and I will tell you when you get each one right.";

export const PRACTICE_CLOSING =
  `Well done, that is all of it. Press the ${COMMAND_KEYS.guide} key any time to hear a part of it again. ` +
  "Swipe right to begin.";

/** Said after enough stalls that the learner is plainly stuck, before moving
 *  on anyway. Being trapped on step one is worse than missing a drill. */
export const PRACTICE_SKIP = "Not to worry, we can come back to that one. Moving on.";

/** How long to wait before nudging, and how many nudges before moving on. */
export const NUDGE_AFTER_MS = 11000;
export const MAX_NUDGES = 2;


/** The drills that rehearse one guide section, in the order they are run.
 *  A section with none (nothing to practise) simply returns an empty list and
 *  the menu goes quiet after reading it. */
export function drillsForSection(section: GuideSectionId): Drill[] {
  return DRILLS.filter((drill) => drill.section === section);
}
