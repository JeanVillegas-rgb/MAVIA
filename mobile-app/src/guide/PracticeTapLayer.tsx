// Catches taps for the one drill that teaches tapping.
//
// Tap-to-answer normally lives on the question card, and during practice there
// is no question card -- so without this the tapping drill could not be done
// at all. It is the same counter the real thing uses (src/input/tapAnswers.ts:
// one tap for A, two for B, three for C, four for D, each spoken as it lands,
// taken as the answer once the taps stop), so what a learner practises here is
// exactly what they will do in a lesson.
//
// It is only mounted while that drill is waiting. Covering the screen for the
// whole run would swallow the swipe drill and every real control underneath
// it, and a learner who fell out of practice would find a dead app.

import React from "react";
import { StyleSheet, View } from "react-native";

import { letterForTapCount, useTapCounter } from "@/input/tapAnswers";
import type { AnswerLetter } from "@/input/brailleKeypad";

type Narrator = {
  speak: (text: string, options?: { onDone?: () => void }) => void;
  stop: () => void;
};

export function PracticeTapLayer({
  active,
  narration,
  onLetter,
}: {
  active: boolean;
  narration: Narrator;
  onLetter: (letter: AnswerLetter) => void;
}) {
  const onTap = useTapCounter({
    // Each tap is read out as it lands, so a learner hears the count instead of
    // trusting an unseen tally -- the same feedback the question card gives.
    onTap: (count) => {
      const letter = letterForTapCount(count);
      if (letter) narration.speak(letter.toUpperCase());
    },
    onSettled: (count) => {
      const letter = letterForTapCount(count);
      if (letter) onLetter(letter);
    },
  });

  // Deliberately does NOT stop narration when it deactivates. It used to, to
  // avoid leaving a half-counted letter being spoken -- but this unmounts the
  // instant the drill is passed, which is the instant the success line starts,
  // so stopping here cancelled that line and the onDone that advances to the
  // next drill. The run then sat there for good. A letter spoken a moment
  // longer costs nothing; a stalled practice costs the whole run.

  if (!active) return null;

  return (
    <View
      style={StyleSheet.absoluteFill}
      // Not a button: the whole screen is the target, which is the point --
      // there is nothing to find. accessible={false} keeps a screen reader
      // from turning it into one more thing to swipe past.
      accessible={false}
      onStartShouldSetResponder={() => true}
      onResponderRelease={onTap}
    />
  );
}
