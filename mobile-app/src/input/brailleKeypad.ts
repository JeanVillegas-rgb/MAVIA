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
  // Say the current narration again from its start. Works on every screen:
  // what "the narration" is differs (a lesson, a question, the list of four
  // courses), but "I did not catch that" is the same request everywhere.
  | { kind: "repeat" }
  // Leave whatever is open and go back one level -- player to lesson list,
  // list to course list, course list to home.
  | { kind: "back" }
  // Page to the next four items in a list. Only means anything on a screen
  // that is reading a list out; elsewhere it is ignored.
  | { kind: "next" }
  // Play the whole spoken guide again from the beginning.
  | { kind: "guide" }
  // Num Lock is off: the numpad is sending navigation keys, not digits.
  | { kind: "numLockOff" };

type Binding = {
  // What the learner presses, for messages and docs.
  label: string;
  // Matched first. A numpad and a keyboard's top-row digits send different
  // codes for the same number, so a binding may list both.
  keycodes: number[];
  // Matched when no keycode did -- a keypad that reports a keycode this list
  // does not know still sends the digit as a character.
  characters: string[];
  action: KeypadAction;
};

const KEYCODE = {
  DIGIT_4: 11,
  DIGIT_5: 12,
  DIGIT_7: 14,
  DIGIT_8: 15,
  NUMPAD_4: 148,
  NUMPAD_5: 149,
  NUMPAD_7: 151,
  NUMPAD_8: 152,
  // The command keys down the right-hand column and along the top. Both the
  // numpad codes and the plain-keyboard ones are listed, because a compact
  // Bluetooth keypad may report either.
  NUMPAD_DIVIDE: 154,
  NUMPAD_MULTIPLY: 155,
  NUMPAD_SUBTRACT: 156,
  NUMPAD_ADD: 157,
  SLASH: 76,
  STAR: 17,
  MINUS: 69,
  PLUS: 81,
  // What numpad 7 / 8 / 4 send when Num Lock is off. Numpad 5 is left out
  // because it has no navigation key: in this state it sends nothing, so a
  // press of it cannot be told apart from no press at all. One of the other
  // three is what raises the warning.
  MOVE_HOME: 122,
  DPAD_UP: 19,
  DPAD_LEFT: 21,
} as const;

// The four answer keys are the square block at the top left of the numpad, read
// the way the options are read out -- left to right, top row then bottom:
//
//   7 -> A   8 -> B
//   4 -> C   5 -> D
//
// Two fingers rest on the two rows, so no key is more than one position from
// another, and nothing needs the reach that 9 and + did.
//
// The command keys sit apart from the answer block so they can never be hit
// by accident while answering:
//
//   /  -> back        *  -> say it again
//   -  -> the guide   +  -> next four
//
// "Say it again" and "back" are the two a learner needs most while a lesson is
// playing, so they take the two keys nearest the answer block.
//
// A and B are also True and False: a True/False question is keyed to the same
// two keys as the first two options of a multiple-choice one, so a finger
// position means the same thing on every question. See QuestionCard's
// optionsFor and the backend's path_answer_is_correct.
export const KEYPAD_BINDINGS: Binding[] = [
  { label: "7", keycodes: [KEYCODE.NUMPAD_7, KEYCODE.DIGIT_7], characters: ["7"], action: { kind: "answer", letter: "a" } },
  { label: "8", keycodes: [KEYCODE.NUMPAD_8, KEYCODE.DIGIT_8], characters: ["8"], action: { kind: "answer", letter: "b" } },
  { label: "4", keycodes: [KEYCODE.NUMPAD_4, KEYCODE.DIGIT_4], characters: ["4"], action: { kind: "answer", letter: "c" } },
  { label: "5", keycodes: [KEYCODE.NUMPAD_5, KEYCODE.DIGIT_5], characters: ["5"], action: { kind: "answer", letter: "d" } },
  { label: "*", keycodes: [KEYCODE.NUMPAD_MULTIPLY, KEYCODE.STAR], characters: ["*"], action: { kind: "repeat" } },
  { label: "/", keycodes: [KEYCODE.NUMPAD_DIVIDE, KEYCODE.SLASH], characters: ["/"], action: { kind: "back" } },
  { label: "-", keycodes: [KEYCODE.NUMPAD_SUBTRACT, KEYCODE.MINUS], characters: ["-"], action: { kind: "guide" } },
  { label: "+", keycodes: [KEYCODE.NUMPAD_ADD, KEYCODE.PLUS], characters: ["+"], action: { kind: "next" } },
  // Never an answer: on a full keyboard these are real navigation keys. Only
  // used to tell the learner why their keypad presses are doing nothing.
  {
    label: "Num Lock off",
    keycodes: [KEYCODE.MOVE_HOME, KEYCODE.DPAD_UP, KEYCODE.DPAD_LEFT],
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
