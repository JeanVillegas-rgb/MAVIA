import { useCallback, useEffect, useRef, useState } from "react";
import * as Speech from "expo-speech";

type SpeakOptions = {
  onDone?: () => void;
};

export function useNarration() {
  const [speakingText, setSpeakingText] = useState<string | null>(null);
  const speakingTextRef = useRef<string | null>(null);

  useEffect(() => {
    return () => {
      Speech.stop();
    };
  }, []);

  const stop = useCallback(() => {
    Speech.stop();
    speakingTextRef.current = null;
    setSpeakingText(null);
  }, []);

  const speak = useCallback((text: string, options?: SpeakOptions) => {
    if (!text) return;
    Speech.stop();
    speakingTextRef.current = text;
    setSpeakingText(text);
    Speech.speak(text, {
      rate: 0.95,
      onDone: () => {
        if (speakingTextRef.current === text) setSpeakingText(null);
        options?.onDone?.();
      },
      onStopped: () => {
        if (speakingTextRef.current === text) setSpeakingText(null);
      },
      onError: () => {
        if (speakingTextRef.current === text) setSpeakingText(null);
      },
    });
  }, []);

  const toggle = useCallback(
    (text: string) => {
      if (speakingText === text) {
        stop();
      } else {
        speak(text);
      }
    },
    [speakingText, speak, stop],
  );

  return { speak, stop, toggle, isSpeaking: speakingText !== null, speakingText };
}

export type NarrationController = ReturnType<typeof useNarration>;
