// Listens to the microphone while a screen is open and runs that screen's
// handler whenever the learner says a voice command. The vocabulary itself --
// which phrases mean which command -- lives in ./commands.ts.

import { useEffect, useRef, useState } from "react";

import { ALL_COMMAND_PHRASES, VoiceCommandId, matchVoiceCommand } from "./commands";

export type VoiceCommandHandlers = Partial<Record<VoiceCommandId, () => void>>;

export type VoiceCommandStatus =
  // Waiting on the permission prompt / recognizer check.
  | "starting"
  | "listening"
  // This build has no speech recognition, the device has no recognizer, or
  // microphone permission was refused. The screen works exactly as before.
  | "unavailable";

// Loaded optionally, not imported. The library looks its native module up the
// moment it is imported, and an installed app built before this dependency
// was added does not contain it -- a plain import would red-screen the lesson
// player on that build instead of just leaving voice commands switched off.
type SpeechModule = typeof import("expo-speech-recognition")["ExpoSpeechRecognitionModule"];
let Speech: SpeechModule | null = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  Speech = require("expo-speech-recognition").ExpoSpeechRecognitionModule as SpeechModule;
} catch {
  Speech = null;
}

// The recognizer ends its session on silence, on some errors, and whenever
// this hook restarts it after a command. Listening should feel continuous, so
// it is started again -- promptly after a normal end, more slowly after an
// error so a recognizer that keeps failing is not hammered.
const RESTART_DELAY_MS = 250;
const ERROR_RESTART_DELAY_MS = 1500;
// Errors that mean listening can never work on this screen; stop trying.
const FATAL_ERRORS = new Set(["not-allowed", "service-not-allowed", "language-not-supported"]);

export function useVoiceCommands(
  handlers: VoiceCommandHandlers,
  { enabled = true }: { enabled?: boolean } = {}
): VoiceCommandStatus {
  const [status, setStatus] = useState<VoiceCommandStatus>(Speech ? "starting" : "unavailable");

  // Read through a ref so the screen can pass a fresh object every render
  // without tearing the microphone session down and back up each time.
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    const speech = Speech;
    if (!enabled || !speech) {
      if (!speech) setStatus("unavailable");
      return;
    }

    let active = true;
    let restartTimer: ReturnType<typeof setTimeout> | null = null;
    let nextDelay = RESTART_DELAY_MS;

    const begin = () => {
      if (!active) return;
      try {
        speech.start({
          lang: "en-US",
          interimResults: true,
          continuous: true,
          maxAlternatives: 3,
          contextualStrings: ALL_COMMAND_PHRASES,
        });
      } catch {
        scheduleRestart(ERROR_RESTART_DELAY_MS);
      }
    };

    const scheduleRestart = (delay: number) => {
      if (!active) return;
      if (restartTimer) clearTimeout(restartTimer);
      restartTimer = setTimeout(begin, delay);
    };

    const subscriptions = [
      speech.addListener("start", () => {
        nextDelay = RESTART_DELAY_MS;
        if (active) setStatus("listening");
      }),

      // Interim results are checked too, so the command lands as soon as it
      // is said rather than after the recognizer decides the sentence ended.
      speech.addListener("result", (event) => {
        if (!active) return;
        for (const result of event.results) {
          const command = matchVoiceCommand(result.transcript);
          if (!command) continue;
          const handler = handlersRef.current[command.id];
          // The transcript keeps growing for the whole session, so the phrase
          // just matched would match again on every later update. Ending the
          // session here clears it; the "end" listener starts a fresh one.
          speech.abort();
          handler?.();
          return;
        }
      }),

      speech.addListener("error", (event) => {
        if (!active) return;
        // Our own abort() after a command reports as an error; it is not one.
        if (event.error === "aborted") return;
        if (FATAL_ERRORS.has(event.error)) {
          active = false;
          setStatus("unavailable");
          return;
        }
        nextDelay = ERROR_RESTART_DELAY_MS;
      }),

      speech.addListener("end", () => {
        scheduleRestart(nextDelay);
      }),
    ];

    (async () => {
      try {
        if (!speech.isRecognitionAvailable()) {
          setStatus("unavailable");
          return;
        }
        const permission = await speech.requestPermissionsAsync();
        if (!permission.granted) {
          setStatus("unavailable");
          return;
        }
        begin();
      } catch {
        setStatus("unavailable");
      }
    })();

    return () => {
      active = false;
      if (restartTimer) clearTimeout(restartTimer);
      for (const subscription of subscriptions) subscription.remove();
      try {
        speech.abort();
      } catch {
        // Already stopped.
      }
    };
  }, [enabled]);

  return status;
}
