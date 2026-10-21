import { useCallback, useEffect, useRef, useState } from "react";
import { useFocusEffect, useNavigation } from "expo-router";

import { registerSilencer, silenceOthers } from "./audioBus";
import * as Speech from "expo-speech";

type SpeakOptions = {
  onDone?: () => void;
};

// expo-speech's onDone/onError are unreliable on web (browser autoplay policy
// and Web Speech API quirks can drop them entirely), and this hook's callers
// gate a whole flow (e.g. auto-advancing past a question) on onDone firing.
// Without a fallback, a speech engine that never calls back strands the
// screen forever. This caps how long we wait: roughly the time the utterance
// should take to read aloud (~11 chars/sec at rate 0.95), with a floor and
// ceiling, plus a margin. The ceiling is only a sanity bound -- it has to
// clear a whole narration version read end to end (the lesson player falls
// back to this hook when a variant has no generated mp3), not just a question
// prompt, or the fallback would fire mid-sentence and skip the student on.
function estimatedSpeechDurationMs(text: string) {
  const estimate = (text.length / 11) * 1000;
  return Math.min(Math.max(estimate, 2500), 120000) + 1500;
}

// expo-speech is ONE voice for the whole app: Speech.stop() silences whoever
// is talking, not just the caller. A screen being left cleans up late -- after
// the slide-out, by when the next screen has started reading -- so a blind
// Speech.stop() there cut the new screen off mid-prompt. So the voice has an
// owner: whichever narrator last spoke. Only the owner may stop it.
let voiceOwner: object | null = null;

// On-device text-to-speech for anything that doesn't have pre-generated
// audio (unlike lesson narration, which has real synthesized files via
// LessonVariant/lesson_playlist) — question prompts, choice confirmations,
// and correct/incorrect feedback. See QuestionCard.tsx for how this is used.
export function useNarration() {
  const [speakingText, setSpeakingText] = useState<string | null>(null);
  const speakingTextRef = useRef<string | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // This narrator's identity as the voice's owner (see voiceOwner).
  const self = useRef({}).current;

  const clearFallbackTimeout = useCallback(() => {
    if (timeoutRef.current != null) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  // Stop the shared voice only if this narrator is the one using it.
  const releaseVoice = useCallback(() => {
    if (voiceOwner !== self) return;
    voiceOwner = null;
    Speech.stop();
  }, [self]);

  useEffect(() => {
    return () => {
      clearFallbackTimeout();
      releaseVoice();
    };
  }, [clearFallbackTimeout, releaseVoice]);

  const stop = useCallback(() => {
    clearFallbackTimeout();
    releaseVoice();
    speakingTextRef.current = null;
    setSpeakingText(null);
  }, [clearFallbackTimeout, releaseVoice]);

  // Anything that ends a screen can silence this instance without holding a
  // reference to it -- the back key lives in the layout and cannot otherwise
  // reach a screen's narrator. `stop` itself is registered so speak() can
  // silence every OTHER source by it.
  useEffect(() => registerSilencer(stop), [stop]);

  // Leaving a screen silences it. A screen navigated away from stays mounted
  // in the router stack, so without this its narration carried on over the
  // screen that replaced it -- and on the way back the two overlapped. Unmount
  // alone is not enough: expo-speech is global and `speak` only cancels the
  // PREVIOUS utterance, never a later one.
  useFocusEffect(
    useCallback(() => () => stop(), [stop])
  );

  // A screen that is not focused may not start speaking either: a fetch or an
  // onDone that lands after the learner left would otherwise talk over the
  // screen they are on now. Asked of the navigator at the moment of speaking,
  // so it does not depend on which effect happened to run first.
  const navigation = useNavigation();

  const speak = useCallback(
    (text: string, options?: SpeakOptions) => {
      if (!text || !navigation.isFocused()) return;
      // One sound at a time: a lesson mp3 or a question clip is not stopped by
      // Speech.stop(), so stop every other source before this one talks.
      silenceOthers(stop);
      clearFallbackTimeout();
      Speech.stop();
      voiceOwner = self;
      speakingTextRef.current = text;
      setSpeakingText(text);

      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        clearFallbackTimeout();
        if (speakingTextRef.current === text) {
          setSpeakingText(null);
          if (voiceOwner === self) voiceOwner = null;
        }
        options?.onDone?.();
      };

      Speech.speak(text, {
        rate: 0.95,
        onDone: finish,
        onStopped: () => {
          // Stopped deliberately (e.g. the learner moved on) — don't fire
          // onDone, just clear the "speaking" state.
          settled = true;
          clearFallbackTimeout();
          if (speakingTextRef.current === text) setSpeakingText(null);
        },
        onError: finish,
      });

      // Safety net: if the platform's speech engine never calls back at all,
      // don't leave the learner stuck waiting for something that never comes.
      timeoutRef.current = setTimeout(finish, estimatedSpeechDurationMs(text));
    },
    [clearFallbackTimeout, stop, navigation, self]
  );

  return { speak, stop, isSpeaking: speakingText !== null, speakingText };
}
