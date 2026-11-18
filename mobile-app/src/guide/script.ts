// The spoken guide: what a learner is told the first time they open the app,
// and what they can pick from when they ask for it again.
//
// It is in four lettered sections rather than one script for a reason learned
// from hearing it read end to end: the whole thing is long, and someone who
// only wants to know which key answers C should not have to sit through
// swiping and paging to reach it. First time through they hear all of it.
// After that the guide key (.) asks: A, the whole guide, or B, choose a part --
// the same A to D, on the same keys, as everything else in the app.
//
// The words a section uses for keys and gestures are built from the constants
// below, which are the same ones the keypad map binds, so the guide cannot
// drift out of step with the app the way written instructions do.

/** The four parts of the guide. Named rather than free strings so a drill can
 *  say which section it rehearses and the compiler checks the pairing. */
export type GuideSectionId = "moving" | "choosing" | "listening" | "answering";

export type GuideSection = {
  id: GuideSectionId;
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
// The stickers on the keypad's command keys, as they are spoken:
//   *  R  repeat          /  P  pause / play
//   -  E  exit (back)     +  N  next four        .  G  guide
export const COMMAND_KEYS = {
  repeat: "R",
  pause: "P",
  back: "E",
  guide: "G",
  next: "N",
} as const;

// Exactly four, so the menu is one page of A to D with no paging of its own.
// Narration only: the guide explains, it does not drill (the drills in
// practice.ts are kept for later). Voice commands are switched off for now,
// so nothing here tells the learner to say anything -- keys and taps only.
export const GUIDE_SECTIONS: GuideSection[] = [
  {
    id: "moving",
    title: "Moving around",
    text:
      "To find your courses, swipe right anywhere on the screen. " +
      `To go back, press the ${COMMAND_KEYS.back} key. ` +
      `To hear this guide again, press the ${COMMAND_KEYS.guide} key.`,
  },
  {
    id: "choosing",
    title: "Choosing a course or topic",
    text:
      "I read your courses out four at a time, and each one gets a letter. " +
      `Press the key with that letter: ${ANSWER_KEYS.a}, ${ANSWER_KEYS.b}, ${ANSWER_KEYS.c} or ${ANSWER_KEYS.d}. ` +
      "I will say your choice back before I open it. " +
      `If the one you want is not among those four, press the ${COMMAND_KEYS.next} key to hear the next four. ` +
      "Once you pick a course, I ask which topic the same way.",
  },
  {
    id: "listening",
    title: "While a lesson is playing",
    text:
      "Each idea of the topic is read to you, one after another. " +
      `To pause, press the ${COMMAND_KEYS.pause} key, and press it again to carry on. ` +
      `To hear something again, press the ${COMMAND_KEYS.repeat} key. ` +
      `I will ask what you want to hear again: press ${ANSWER_KEYS.a} for the topic, ` +
      `or ${ANSWER_KEYS.b} for the question.`,
  },
  {
    id: "answering",
    title: "Answering a question",
    text:
      "After an idea, I read you a question and its choices. There are two ways to answer. " +
      `Press the key marked with your letter: ${ANSWER_KEYS.a}, ${ANSWER_KEYS.b}, ` +
      `${ANSWER_KEYS.c} or ${ANSWER_KEYS.d}. For true or false, ${ANSWER_KEYS.a} is true and ${ANSWER_KEYS.b} is false. ` +
      "Or tap anywhere on the screen: once for A, twice for B, three times for C, four times for D. " +
      "I say each letter as you tap, and your answer is taken a moment after you stop. " +
      "If your answer is not right, I will explain the idea again in a different way before asking you again.",
  },
];

// What the guide key asks first: the whole guide, or one part of it.
export const GUIDE_ENTRY_QUESTION =
  `Guide. Press ${ANSWER_KEYS.a} to hear the whole guide, or ${ANSWER_KEYS.b} to choose one part. ` +
  `Press the ${COMMAND_KEYS.guide} key again to close the guide.`;

// What the part menu says before reading the four titles.
export const GUIDE_MENU_QUESTION = "Which part would you like to hear?";

// The whole guide is topped and tailed: someone who has never used the app
// cannot pick a part usefully, so the first time through they hear all of it.
export const GUIDE_WELCOME =
  "Welcome to MAVIA. I will explain how to move around and how to answer. " +
  `You can stop me at any time by pressing the ${COMMAND_KEYS.guide} key.`;

export const GUIDE_CLOSING =
  `That is everything. Press the ${COMMAND_KEYS.guide} key whenever you want to hear this again. ` +
  "Swipe right to begin.";

export const FIRST_RUN_PARTS: string[] = [
  GUIDE_WELCOME,
  ...GUIDE_SECTIONS.map((section) => `${section.title}. ${section.text}`),
  GUIDE_CLOSING,
];

export function guideSection(id: string): GuideSection | undefined {
  return GUIDE_SECTIONS.find((section) => section.id === id);
}
