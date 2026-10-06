import React, { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

import { useNarration } from "@/hooks/useNarration";
import { useAudioPlayer } from "@/hooks/useAudioPlayer";
import { resolveMediaUrl } from "@/api/client";
import { useBrailleKeypad } from "@/input/useBrailleKeypad";
import { letterForTapCount, useTapCounter } from "@/input/tapAnswers";
import { useScreenReaderEnabled } from "@/hooks/useScreenReaderEnabled";
import { useOneTimeGuidePart } from "@/guide/useGuide";
import { useGuideBusy } from "@/guide/GuideActivity";
import { ANSWER_KEYS } from "@/guide/script";
import { colors, radii, spacing } from "@/theme";

// Mirrors lessons.Question from the API.
export type Question = {
  id: number;
  order: number;
  prompt: string;
  question_type: "open_ended" | "true_false" | "multiple_choice" | string;
  choices: string[];
  correct_answer: string;
  // The question and its options as a recorded clip, in the lesson's voice.
  // "" (or absent): read by the device voice instead.
  audio_url?: string;
};

export type SubmitResult = { is_correct: boolean; mastery: number; completed: boolean };

type Option = { key: string; label: string };
const LETTERS = ["a", "b", "c", "d", "e", "f"];

// Every option is addressed by its letter, True/False included: the braille
// numpad has one physical key per letter, so the same finger position has to
// mean the same thing whatever kind of question is on screen. A = True,
// B = False. The backend accepts those letters for TF questions --
// see adaptive/services.py::path_answer_is_correct.
function optionsFor(question: Question): Option[] {
  if (question.question_type === "true_false") {
    return [
      { key: LETTERS[0], label: "True" },
      { key: LETTERS[1], label: "False" },
    ];
  }
  return (question.choices ?? []).map((label, index) => ({ key: LETTERS[index], label }));
}

type Props = {
  question: Question;
  index: number;
  total: number;
  onSubmit: (answer: string) => Promise<SubmitResult>;
  onNext: () => void;
  // Bumped by the screen when the learner asks to hear it again (the
  // "repeat the question" voice command). Re-reads the question in place -- unlike remounting the
  // card, an answer already given is kept.
  repeatSignal?: number;
  // Shown in place of the question's text and options. The question is still
  // read aloud and answered the same ways (voice, keypad, tap counting on the
  // whole card); it just isn't put on screen -- the lesson player passes its
  // greyed-out player here so the screen stays the player throughout.
  face?: React.ReactNode;
  // True while the screen is asking something else (the repeat key's "the
  // topic, or the question?"): A and B answer THAT, so the answer keys, taps
  // and buttons here must not submit an answer to the question meanwhile.
  inputPaused?: boolean;
};

export default function QuestionCard({ question, index, total, onSubmit, onNext, repeatSignal = 0, face, inputPaused = false }: Props) {
  const options = useMemo(() => optionsFor(question), [question]);
  const [selected, setSelected] = useState<string | null>(null);
  const [result, setResult] = useState<SubmitResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const narration = useNarration();
  const advancedRef = useRef(false);
  // The question's own recording. Anything else the card says stops it first
  // (say() below): expo-speech cannot stop an audio file.
  const clip = useAudioPlayer(() => {
    if (openEnded) advanceOnce();
  });
  // A clip that will not load is read by the device voice instead.
  const clipFailedRef = useRef(false);

  // True while the question itself is being read (its clip, or the device voice
  // reading it). Answering is ignored meanwhile: an answer's read-back would talk
  // over the question, and a clip still loading could start playing over it.
  // Released when the reading ends -- finished, or stopped with the pause key, so
  // a learner who cuts it short can still answer. Mirrored into a ref because a
  // keypad can deliver a press before React re-renders.
  const [questionReading, setQuestionReadingState] = useState(false);
  const questionReadingRef = useRef(false);
  // The reading has actually started making sound (a clip still loading has not).
  const readingStartedRef = useRef(false);
  const readingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  function setQuestionReading(on: boolean) {
    questionReadingRef.current = on;
    setQuestionReadingState(on);
    if (readingTimeoutRef.current) clearTimeout(readingTimeoutRef.current);
    readingTimeoutRef.current = null;
    readingStartedRef.current = false;
    // Safety net: a clip that never starts and never errors must not lock the question.
    if (on) readingTimeoutRef.current = setTimeout(() => setQuestionReading(false), 90000);
  }
  useEffect(() => {
    if (!questionReadingRef.current) return;
    if (clip.isPlaying || narration.isSpeaking) readingStartedRef.current = true;
    else if (readingStartedRef.current) setQuestionReading(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clip.isPlaying, narration.isSpeaking]);
  useEffect(() => () => {
    if (readingTimeoutRef.current) clearTimeout(readingTimeoutRef.current);
  }, []);

  function say(text: string, options?: { onDone?: () => void }) {
    clip.stop();
    narration.speak(text, options);
  }
  // The verdict has to wait for the read-back of the chosen answer to finish.
  // speak() stops whatever is talking, so a fast reply from the server would
  // otherwise cut "Answered A. Solid." off mid-word. These two track which of
  // the pair happened first; whichever finishes last plays the verdict.
  const readBackDoneRef = useRef(false);
  // `advance` is false when the submit failed: the student stays on this
  // question to try again, so the verdict must not move them on.
  const verdictRef = useRef<{ text: string; advance: boolean } | null>(null);

  const answered = result !== null;
  const openEnded = options.length === 0;

  function advanceOnce() {
    if (advancedRef.current) return;
    advancedRef.current = true;
    onNext();
  }

  function speakVerdictWhenReady() {
    if (!readBackDoneRef.current || verdictRef.current === null) return;
    const { text, advance } = verdictRef.current;
    verdictRef.current = null;
    say(text, advance ? { onDone: advanceOnce } : undefined);
  }

  // "A. Solid. B. Liquid." -- the letter is the key they press, so it is
  // read with every option, not just implied by the order.
  // How to answer, said once, in front of the very first question a learner
  // ever reaches -- where it is about to be useful -- rather than only in the
  // guide at launch. Spliced into the same utterance as the question, because
  // a second speak() would cut the first one off.
  const answeringTip = useOneTimeGuidePart("answering");

  function readQuestionAloud({ withTip = false }: { withTip?: boolean } = {}) {
    const choiceText = options.map((o) => `${o.key.toUpperCase()}. ${o.label}.`).join(" ");
    const body = choiceText ? `${question.prompt} ${choiceText}` : question.prompt;
    // Only the automatic first read carries the tip. Asking to hear the
    // question again means the question, not the instructions.
    const tip = withTip && !openEnded ? answeringTip.take() : "";
    if (!openEnded) setQuestionReading(true);
    if (question.audio_url && !clipFailedRef.current) {
      const playClip = () => {
        if (choosingRef.current) return;
        // The tip has just finished: the clip, not the gap before it loads, is the reading.
        readingStartedRef.current = false;
        clip.load(resolveMediaUrl(question.audio_url!), true);
      };
      // The tip is not part of the recording: say it, then play the question.
      if (tip) say(tip, { onDone: playClip });
      else {
        narration.stop();
        playClip();
      }
      return;
    }
    say(tip ? `${tip} ${body}` : body, {
      onDone: openEnded ? advanceOnce : undefined,
    });
  }

  useEffect(() => {
    if (!clip.error || clipFailedRef.current) return;
    clipFailedRef.current = true;
    if (!choosingRef.current) readQuestionAloud();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clip.error]);

  // Read the new question (and its choices) aloud as soon as it appears.
  // Open-ended questions have nothing to grade -- move on as soon as the
  // prompt's been read instead of waiting on a tap that never comes from
  // choose() below.
  //
  // Waits on the tip's storage check: reading the question the instant it
  // mounts would settle the wording before we know whether the tip belongs in
  // front of it. `ready` flips once, so later questions are unaffected.
  const guideBusy = useGuideBusy();
  useEffect(() => {
    // Same rule as the lesson audio: never read a question over the guide.
    if (guideBusy || !answeringTip.ready) return;
    advancedRef.current = false;
    readQuestionAloud({ withTip: true });
    return () => {
      narration.stop();
      clip.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [question.id, answeringTip.ready, guideBusy]);

  // Asked to hear it again. Only while the question is still open: once an
  // answer is in, the read-back and verdict are already speaking and the card
  // is about to move on, so re-reading would talk over them.
  const lastRepeat = useRef(repeatSignal);
  useEffect(() => {
    if (repeatSignal === lastRepeat.current) return;
    lastRepeat.current = repeatSignal;
    if (answered || submitting) return;
    readQuestionAloud();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repeatSignal]);

  // Set synchronously, unlike `submitting`: a keypad can deliver two presses
  // before React re-renders, and the state flags would still read false for
  // the second one -- submitting the same question twice.
  const choosingRef = useRef(false);
  const inputPausedRef = useRef(inputPaused);
  // The guide being open pauses input too: its A / B / C / D are the guide's.
  inputPausedRef.current = inputPaused || guideBusy;

  async function choose(key: string) {
    if (choosingRef.current || submitting || answered || questionReadingRef.current) return;
    choosingRef.current = true;
    setSelected(key);
    setSubmitting(true);
    setError(null);
    readBackDoneRef.current = false;
    verdictRef.current = null;

    // Read the choice back immediately, before the network round trip: the
    // student needs to know which key registered without waiting on a server.
    const option = options.find((o) => o.key === key);
    say(
      option ? `Answered ${option.key.toUpperCase()}. ${option.label}.` : `Answered ${key}.`,
      {
        onDone: () => {
          readBackDoneRef.current = true;
          speakVerdictWhenReady();
        },
      }
    );

    try {
      const res = await onSubmit(key);
      setResult(res);
      verdictRef.current = { text: res.is_correct ? "Correct." : "Not quite.", advance: true };
      speakVerdictWhenReady();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Couldn't submit your answer.";
      // Not answered after all -- let the learner try again.
      choosingRef.current = false;
      setSelected(null);
      setError(message);
      // Say it too -- a silent failure leaves a student who cannot read the
      // red text waiting on a question that will never move.
      verdictRef.current = { text: "That answer didn't send. Please try again.", advance: false };
      speakVerdictWhenReady();
    } finally {
      setSubmitting(false);
    }
  }

  // Braille keypad over Bluetooth: the four answer keys (ANSWER_KEYS) answer
  // A, B, C and D (the
  // mapping is src/input/brailleKeypad.ts). Answering goes through choose(),
  // exactly like a tap, so the read-back and verdict are identical.
  const numLockWarnedRef = useRef(false);
  useBrailleKeypad(
    (action) => {
      if (action.kind === "numLockOff") {
        // Once per question: a held or repeated arrow should not become a loop.
        if (numLockWarnedRef.current) return;
        numLockWarnedRef.current = true;
        say(
          `Number lock is off. Press Num Lock, then answer with ${ANSWER_KEYS.a}, ${ANSWER_KEYS.b}, ${ANSWER_KEYS.c}, or ${ANSWER_KEYS.d}.`
        );
        return;
      }
      // The command keys (* / - +) are handled a level up, on the screen, so
      // that they work the same while a lesson plays as they do on a question.
      // Only an answer key means anything here.
      if (action.kind !== "answer") return;
      if (inputPausedRef.current || questionReadingRef.current) return;
      if (choosingRef.current || answered || submitting) return;
      const option = options.find((o) => o.key === action.letter);
      if (!option) {
        // e.g. 5 (D) on a True/False question: say so rather than do nothing.
        say(`There is no option ${action.letter.toUpperCase()}.`);
        return;
      }
      choose(option.key);
    },
    { enabled: !openEnded }
  );

  // Tap answering, for when the keypad is not at hand: tap the question once
  // for A, twice for B, three times for C, four for D (src/input/tapAnswers.ts).
  // Off under a screen reader, where a tap only moves focus and the option
  // buttons below are the way to answer.
  const screenReaderOn = useScreenReaderEnabled();
  const tapMode = !openEnded && !screenReaderOn;
  const onQuestionTap = useTapCounter({
    onTap: (count) => {
      if (inputPausedRef.current || questionReadingRef.current || choosingRef.current || answered || submitting) return;
      if (count > options.length) {
        say(
          options.length === 1 ? "There is only one option." : `There are only ${options.length} options.`
        );
        return;
      }
      // The running count, spoken as it lands, so it can be heard, not guessed.
      say(options[count - 1].key.toUpperCase());
    },
    onSettled: (count) => {
      if (inputPausedRef.current || questionReadingRef.current || choosingRef.current || answered || submitting) return;
      const letter = letterForTapCount(count);
      const option = letter ? options.find((o) => o.key === letter) : undefined;
      // Too many taps was already announced; nothing is submitted.
      if (option) choose(option.key);
    },
  });

  // The verdict is spoken only (speakVerdictWhenReady), never shown.
  const status = (
    <>
      {submitting && <ActivityIndicator color={colors.brand600} />}
      {error && <Text style={styles.error}>{error}</Text>}
    </>
  );

  // The face replaces the question on screen. Under a screen reader, where a
  // tap only moves focus, compact letter buttons stay as a touch way to answer
  // (each announced with its option); everyone else taps the face itself.
  const faceContent = face ? (
    <>
      {face}
      <Text style={[styles.counter, styles.faceCounter]}>
        Question {index + 1} of {total} · {questionReading ? "listen to the question" : "waiting for your answer"}
      </Text>
      {!tapMode && !openEnded && (
        <View style={styles.letterRow}>
          {options.map((option) => (
            <Pressable
              key={option.key}
              disabled={answered || submitting || inputPaused || questionReading}
              onPress={() => choose(option.key)}
              accessibilityRole="button"
              accessibilityLabel={`${option.key.toUpperCase()}. ${option.label}`}
              style={[styles.letterButton, option.key === selected && styles.optionSelected]}
            >
              <Text style={styles.optionKeyText}>{option.key.toUpperCase()}</Text>
            </Pressable>
          ))}
        </View>
      )}
      {status}
    </>
  ) : null;

  const content = faceContent ?? (
    <>
      <Text style={styles.counter}>
        Question {index + 1} of {total}
      </Text>
      <Text style={styles.prompt} accessibilityRole="header">
        {question.prompt}
      </Text>

      {openEnded ? (
        <Text style={styles.note}>
          This is an open-ended question — read it aloud with your teacher. It isn’t
          auto-graded here.
        </Text>
      ) : (
        <View style={styles.options}>
          {options.map((option) => {
            const isCorrect =
              answered && option.key.toLowerCase() === question.correct_answer.trim().toLowerCase();
            const isWrongPick = answered && option.key === selected && !isCorrect;
            const rowStyle = [
              styles.option,
              option.key === selected && !answered && styles.optionSelected,
              isCorrect && styles.optionCorrect,
              isWrongPick && styles.optionWrong,
            ];
            const row = (
              <>
                <View style={styles.optionKey}>
                  <Text style={styles.optionKeyText}>{option.key.toUpperCase()}</Text>
                </View>
                <Text style={styles.optionLabel}>{option.label}</Text>
              </>
            );
            // In tap mode an option is a label, not a button: every tap on the
            // card counts, wherever it lands. A child cannot see where the
            // buttons are, and a stray touch on one must not answer outright.
            return tapMode ? (
              <View key={option.key} style={rowStyle}>
                {row}
              </View>
            ) : (
              <Pressable
                key={option.key}
                disabled={answered || submitting || inputPaused || questionReading}
                onPress={() => choose(option.key)}
                accessibilityRole="button"
                style={rowStyle}
              >
                {row}
              </Pressable>
            );
          })}
        </View>
      )}

      {status}
    </>
  );

  // In tap mode the whole card is the tap pad, stretched to fill the screen
  // below the concept header -- the back button stays outside it.
  const cardStyle = face ? styles.faceCard : styles.card;
  return tapMode ? (
    <Pressable
      style={[cardStyle, styles.tapPad]}
      onPress={onQuestionTap}
      accessibilityLabel="Tap once for A, twice for B, three times for C, four times for D."
    >
      {content}
    </Pressable>
  ) : (
    <View style={cardStyle}>{content}</View>
  );
}

const styles = StyleSheet.create({
  tapPad: { flexGrow: 1 },
  faceCard: { gap: spacing.md, alignItems: "stretch" },
  faceCounter: { textAlign: "center" },
  letterRow: { flexDirection: "row", justifyContent: "center", gap: spacing.md },
  letterButton: {
    width: 48,
    height: 48,
    borderRadius: radii.pill,
    borderWidth: 2,
    borderColor: "transparent",
    backgroundColor: colors.brand600,
    alignItems: "center",
    justifyContent: "center",
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.md,
    padding: spacing.lg,
    gap: spacing.md,
  },
  counter: {
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 1,
    textTransform: "uppercase",
    color: colors.faint,
  },
  prompt: { fontSize: 20, fontWeight: "700", lineHeight: 27, color: colors.ink },
  note: { fontSize: 13, color: colors.muted, fontStyle: "italic" },
  options: { gap: spacing.sm },
  option: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: colors.panel,
    borderRadius: radii.sm,
    borderWidth: 2,
    borderColor: "transparent",
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  optionSelected: { borderColor: colors.brand400 },
  optionCorrect: { borderColor: colors.success, backgroundColor: colors.successBg },
  optionWrong: { borderColor: colors.danger, backgroundColor: colors.dangerBg },
  optionKey: {
    width: 24,
    height: 24,
    borderRadius: radii.pill,
    backgroundColor: colors.brand600,
    alignItems: "center",
    justifyContent: "center",
  },
  optionKeyText: { fontSize: 11, fontWeight: "800", color: colors.white },
  optionLabel: { flex: 1, fontSize: 14, fontWeight: "600", color: colors.ink },
  error: { fontSize: 12, fontWeight: "600", color: colors.danger },
});
