// Listens to a Bluetooth keypad while a screen is open and hands that screen
// each bound key press. Which key means what lives in ./brailleKeypad.ts.
//
// How Android delivers the keys: expo-key-event adds an invisible view to the
// screen and gives it input focus; hardware key presses go to whatever holds
// focus. Tapping a control on screen can take focus away, after which presses
// stop arriving until listening restarts -- which it does every time this hook
// mounts, i.e. on every new question. Someone answering by keypad alone never
// moves focus, so this only affects mixing taps and keys on one question.

import { useEffect, useRef } from "react";

import { KeypadAction, keypadActionFor } from "./brailleKeypad";

type RawKeyPress = { key: string; character?: string | null; repeat?: boolean };
type KeyEventModule = {
  addListener(event: "onKeyPress", listener: (event: RawKeyPress) => void): { remove(): void };
  startListening(): void;
  stopListening(): void;
};

// Loaded optionally, for the same reason as the speech module in
// src/voice/useVoiceCommands.ts: the native module is looked up at import time,
// and an app built before this dependency was added would otherwise red-screen
// instead of simply having no keypad support. The package's own hooks are not
// used for the same reason -- and because in debug builds they reload the app
// whenever "R" is pressed.
let Keys: KeyEventModule | null = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  Keys = require("expo-key-event/build/ExpoKeyEventModule").default as KeyEventModule;
} catch {
  Keys = null;
}

// Listening is global to the app, but more than one screen wants keys at once:
// the student layout binds the command keys (back, guide) while a question
// binds the answer keys. Each hook used to call stopListening() on unmount,
// which stopped listening for everyone -- so closing a question silently
// killed the layout's keys too. Counting mounts fixes that: the native
// listener starts on the first and stops only on the last.
let listeners = 0;

function acquire(keys: KeyEventModule) {
  listeners += 1;
  // Start on EVERY mount, not only the first. startListening() is also what
  // re-takes native input focus, and focus is lost by ordinary things -- a tap
  // on a control, a screen transition. With "first mount only", the layout's
  // always-on listener pinned the count at 1 forever, so a picker mounting
  // later (a question, the guide menu) could never get focus back and its keys
  // silently went nowhere while the layout's still worked.
  try {
    keys.startListening();
  } catch {
    // No activity to attach to (app backgrounded): nothing to listen with.
  }
}

function release(keys: KeyEventModule) {
  listeners = Math.max(0, listeners - 1);
  if (listeners > 0) return;
  try {
    keys.stopListening();
  } catch {
    // Already detached.
  }
}

/** Returns whether keypad input is available in this build. */
export function useBrailleKeypad(
  onAction: (action: KeypadAction) => void,
  { enabled = true }: { enabled?: boolean } = {}
): boolean {
  // Through a ref: the screen can pass a fresh callback every render without
  // tearing the native listener down and back up.
  const onActionRef = useRef(onAction);
  onActionRef.current = onAction;

  useEffect(() => {
    const keys = Keys;
    if (!enabled || !keys) return;

    const subscription = keys.addListener("onKeyPress", (event) => {
      // Holding a key down sends a stream of repeats; one press is one answer.
      if (event.repeat) return;
      const action = keypadActionFor(event);
      if (action) onActionRef.current(action);
    });
    acquire(keys);

    return () => {
      subscription.remove();
      release(keys);
    };
  }, [enabled]);

  return Keys !== null;
}
