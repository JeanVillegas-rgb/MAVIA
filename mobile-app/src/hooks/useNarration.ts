import { useCallback, useEffect, useRef, useState } from "react";
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

// On-device text-to-speech for anything that doesn't have pre-generated
// audio (unlike lesson narration, which has real synthesized files via
// LessonVariant/lesson_playlist) — question prompts, choice confirmations,
// and correct/incorrect feedback. See QuestionCard.tsx for how this is used.
export function useNarration() {
  const [speakingText, setSpeakingText] = useState<string | null>(null);
  const speakingTextRef = useRef<string | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearFallbackTimeout = useCallback(() => {
    if (timeoutRef.current != null) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      clearFallbackTimeout();
      Speech.stop();
    };
  }, [clearFallbackTimeout]);

  const stop = useCallback(() => {
    clearFallbackTimeout();
    Speech.stop();
    speakingTextRef.current = null;
    setSpeakingText(null);
  }, [clearFallbackTimeout]);

  const speak = useCallback(
    (text: string, options?: SpeakOptions) => {
      if (!text) return;
      clearFallbackTimeout();
      Speech.stop();
      speakingTextRef.current = text;
      setSpeakingText(text);

      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        clearFallbackTimeout();
        if (speakingTextRef.current === text) setSpeakingText(null);
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
    [clearFallbackTimeout]
  );

  return { speak, stop, isSpeaking: speakingText !== null, speakingText };
}

export type NarrationController = ReturnType<typeof useNarration>;
