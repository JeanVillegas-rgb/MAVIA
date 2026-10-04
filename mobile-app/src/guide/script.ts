// The spoken guide: what a learner is told the first time they open the app,
// and what they can pick from when they ask for it again.
//
// It is in four lettered sections rather than one script for a reason learned
// from hearing it read end to end: the whole thing is long, and someone who
// only wants to know which key answers C should not have to sit through
// swiping and paging to reach it. First time through they hear all of it.
// Every time after, pressing minus offers the four sections by letter -- the
// same A to D, on the same keys, as everything else in the app -- and reads
// only the one they ask for.
//
// The words a section uses for keys and gestures are built from the constants
// below, which are the same ones the keypad map binds, so the guide cannot
// drift out of step with the app the way written instructions do.

export type GuideSection = {
  id: string;
  // Read out in the "which part?" menu, so it has to be short.
  title: string;
  text: string;
};

// What the guide CALLS each key, which is whatever the learner's fingers find
// printed on it -- not what the key sends. Braille caps are overlaid on the
// numpad, so the command keys are read out by their cap letter rather than as
// arithmetic: saying "divide" to someone feeling a cap marked E is worse than
// useless. The keycodes these names belong to never change, only the words
// do, and every spoken mention of a key in the app comes from here.
// The answer keys carry braille caps marked A to D, so that is what they are
// called. The digits underneath (7, 8, 4, 5) are what the keypad sends and
// what brailleKeypad.ts binds; a learner never needs to hear them.
export const ANSWER_KEYS = { a: "A", b: "B", c: "C", d: "D" } as const;
export const COMMAND_KEYS = {
  repeat: "R",
  back: "E",
  guide: "G",
  next: "N",
} as const;

// Exactly four, so the menu is one page of A to D with no paging of its own.
export const GUIDE_SECTIONS: GuideSection[] = [
  {
    id: "moving",
    title: "Moving around",
    text:
      "To find a lesson, swipe right anywhere on the screen. " +
      `To go back at any time, from anywhere, press the ${COMMAND_KEYS.back} key, or say, go back. ` +
      `To hear this guide again, press the ${COMMAND_KEYS.guide} key.`,
  },
  {
    id: "choosing",
    title: "Choosing a course or lesson",
    text:
      "I read your courses out four at a time, and each one gets a letter. " +
      `Say the letter you want, or press its key: the four keys are marked ${ANSWER_KEYS.a}, ` +
      `${ANSWER_KEYS.b}, ${ANSWER_KEYS.c} and ${ANSWER_KEYS.d}. ` +
      "If the one you want is not among those four, say next four, " +
      `or press the ${COMMAND_KEYS.next} key. ` +
      "Once you pick a course, I ask which lesson the same way.",
  },
  {
    id: "listening",
    title: "While a lesson is playing",
    text:
      "Your lesson is read to you. " +
      "If it goes too quickly, say, can you repeat the lesson. " +
      "While a question is open, say, can you repeat that question. " +
      `Or press the ${COMMAND_KEYS.repeat} key at any time to hear whatever is playing from the start.`,
  },
  {
    id: "answering",
    title: "Answering a question",
    text:
      "There are two ways to answer. " +
      `Press the key marked with your letter: ${ANSWER_KEYS.a}, ${ANSWER_KEYS.b}, ` +
      `${ANSWER_KEYS.c} or ${ANSWER_KEYS.d}. ` +
      "Or tap anywhere on the screen: once for A, twice for B, three times for C, four times for D. " +
      "I say each letter as you tap, and your answer is taken a moment after you stop.",
  },
];

// What the menu says before reading the four titles. Kept here beside the
// sections it describes.
export const GUIDE_MENU_QUESTION = "Which part would you like to hear?";

// The first time through, the sections are topped and tailed rather than
// offered: someone who has never used the app cannot pick a section usefully.
const WELCOME =
  "Welcome to MAVIA. I will explain how to move around and how to answer. " +
  "You can stop me at any time by pressing any key.";

const CLOSING =
  `That is everything. Press the ${COMMAND_KEYS.guide} key whenever you want a part of this again, ` +
  "and I will let you pick just the part you need. Swipe right to begin.";

export const FIRST_RUN_PARTS: string[] = [
  WELCOME,
  ...GUIDE_SECTIONS.map((section) => section.text),
  CLOSING,
];

export function guideSection(id: string): GuideSection | undefined {
  return GUIDE_SECTIONS.find((section) => section.id === id);
}
