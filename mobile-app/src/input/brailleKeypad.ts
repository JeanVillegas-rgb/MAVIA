// Braille keypad: which physical key means what.
//
// This file is the whole mapping. It knows nothing about screens or the
// Bluetooth connection, so keys can be added or moved without touching either.
//
// To bind another key:
//   1. Add an action to `KeypadAction` if it does something new.
//   2. Add an entry to `KEYPAD_BINDINGS`: the Android keycodes and/or the
//      character the key produces.
//   3. Handle the action where it should work -- see
//      src/components/QuestionCard.tsx. A screen that ignores an action is fine.
//
// Keycodes are Android's `KeyEvent.KEYCODE_*` values, read from the SDK's
// android.jar rather than recalled (javap -constants android.view.KeyEvent).

export type AnswerLetter = "a" | "b" | "c" | "d";

export type KeypadAction =
  | { kind: "answer"; letter: AnswerLetter }
  // Num Lock is off: the numpad is sending navigation keys, not digits.
  | { kind: "numLockOff" };

type Binding = {
  // What the learner presses, for messages and docs.
  label: string;
  // Matched first. A numpad and a keyboard's top-row digits send different
  // codes for the same number, so a binding may list both.
  keycodes: number[];
  // Matched when no keycode did -- e.g. "+" typed as Shift + "=".
  characters: string[];
  action: KeypadAction;
};

const KEYCODE = {
  DIGIT_7: 14,
  DIGIT_8: 15,
  DIGIT_9: 16,
  PLUS: 81,
  NUMPAD_7: 151,
  NUMPAD_8: 152,
  NUMPAD_9: 153,
  NUMPAD_ADD: 157,
  // What numpad 7 / 8 / 9 send when Num Lock is off.
  MOVE_HOME: 122,
  DPAD_UP: 19,
  PAGE_UP: 92,
} as const;

// A and B are also True and False: a True/False question is keyed to the same
// two keys as the first two options of a multiple-choice one, so a finger
// position means the same thing on every question. See QuestionCard's
// optionsFor and the backend's path_answer_is_correct.
export const KEYPAD_BINDINGS: Binding[] = [
  { label: "7", keycodes: [KEYCODE.NUMPAD_7, KEYCODE.DIGIT_7], characters: ["7"], action: { kind: "answer", letter: "a" } },
  { label: "8", keycodes: [KEYCODE.NUMPAD_8, KEYCODE.DIGIT_8], characters: ["8"], action: { kind: "answer", letter: "b" } },
  { label: "9", keycodes: [KEYCODE.NUMPAD_9, KEYCODE.DIGIT_9], characters: ["9"], action: { kind: "answer", letter: "c" } },
  { label: "+", keycodes: [KEYCODE.NUMPAD_ADD, KEYCODE.PLUS], characters: ["+"], action: { kind: "answer", letter: "d" } },
  // Never an answer: on a full keyboard these are real navigation keys. Only
  // used to tell the learner why their keypad presses are doing nothing.
  {
    label: "Num Lock off",
    keycodes: [KEYCODE.MOVE_HOME, KEYCODE.DPAD_UP, KEYCODE.PAGE_UP],
    characters: [],
    action: { kind: "numLockOff" },
  },
];

/** The action a key press asks for, or `null` for a key with no binding.
 *  `key` is the Android keycode as a string, as the native module sends it. */
export function keypadActionFor(event: { key: string; character?: string | null }): KeypadAction | null {
  const keycode = Number(event.key);
  if (Number.isFinite(keycode)) {
    const byCode = KEYPAD_BINDINGS.find((binding) => binding.keycodes.includes(keycode));
    if (byCode) return byCode.action;
  }
  if (event.character) {
    const byCharacter = KEYPAD_BINDINGS.find((binding) => binding.characters.includes(event.character!));
    if (byCharacter) return byCharacter.action;
  }
  return null;
}
