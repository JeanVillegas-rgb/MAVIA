// Voice commands: what a learner can say, and which command each phrase means.
//
// This file is the whole vocabulary. It knows nothing about screens, audio or
// the microphone, so it can grow without touching either.
//
// To add a command:
//   1. Add its id to `VoiceCommandId`.
//   2. Add an entry to `VOICE_COMMANDS` listing the phrases that trigger it.
//   3. On the screen where it should do something, pass a handler for that id
//      to `useVoiceCommands` -- see app/(student)/home/[courseId]/[lessonId].tsx.
// A screen that has no handler for a command simply ignores it, so a command
// can exist before every screen supports it.

export type VoiceCommandId = "repeatTopic" | "repeatQuestion";

export type VoiceCommand = {
  id: VoiceCommandId;
  // What the command does, in the learner's words.
  description: string;
  // Matched against what the recognizer heard, ignoring case and punctuation.
  // A phrase counts when its words appear together anywhere in the utterance,
  // so "um, can you repeat the question?" still triggers "can you repeat the question".
  //
  // Keep phrases to several words. The microphone also hears the lesson
  // coming out of the phone's own speaker, and a single common word ("repeat",
  // "again") will turn up in lesson text sooner or later and fire by accident.
  phrases: string[];
};

// Topic and question are separate commands on purpose: they are two
// different recordings, and a learner stuck on a question may want the lesson
// again rather than the question read twice.
export const VOICE_COMMANDS: VoiceCommand[] = [
  {
    id: "repeatTopic",
    description: "Hear the topic again",
    phrases: [
      "can you repeat the topic",
      "please repeat the topic",
      "repeat the topic",
      "can you say the topic again",
      "say the topic again",
    ],
  },
  {
    id: "repeatQuestion",
    description: "Hear the question again",
    phrases: [
      "can you repeat the question",
      "please repeat the question",
      "repeat the question",
      "can you say the question again",
      "say the question again",
    ],
  },
];

function normalize(text: string): string {
  return ` ${text
    .toLowerCase()
    .replace(/[’']/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()} `;
}

// Pre-normalized once; a command list is small but recognition fires often.
const INDEX = VOICE_COMMANDS.flatMap((command) =>
  command.phrases.map((phrase) => ({ command, phrase: normalize(phrase) }))
);

/** The command a heard utterance asks for, or `null`. When more than one
 *  matches, the longest phrase wins -- the more specific reading of what was
 *  said. */
export function matchVoiceCommand(transcript: string): VoiceCommand | null {
  const heard = normalize(transcript);
  let best: { command: VoiceCommand; phrase: string } | null = null;
  for (const entry of INDEX) {
    if (heard.includes(entry.phrase) && (!best || entry.phrase.length > best.phrase.length)) {
      best = entry;
    }
  }
  return best?.command ?? null;
}

/** Every phrase, as a hint list for the recognizer: biasing it toward the
 *  words it is listening for makes a short command far likelier to be heard
 *  correctly over lesson audio. */
export const ALL_COMMAND_PHRASES = VOICE_COMMANDS.flatMap((command) => command.phrases);
