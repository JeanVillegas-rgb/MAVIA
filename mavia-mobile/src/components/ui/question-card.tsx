import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Platform, Pressable, StyleSheet, Text, View } from "react-native";

import type { LessonQuestion, SubmitResponseResult } from "@/api/types";
import type { NarrationController } from "@/hooks/use-narration";
import { colors, fonts, radii, spacing } from "@/theme";

type Option = { key: string; label: string };

const TF_OPTIONS: Option[] = [
  { key: "true", label: "True" },
  { key: "false", label: "False" },
];

const TIER_LABELS: Record<string, string> = {
  remember: "Remember",
  understand: "Understand",
  apply: "Apply & Analyze",
  analyze: "Apply & Analyze",
  evaluate: "Evaluate",
  create: "Create",
};

// Answers map to a Bluetooth numpad overlaid with braille keycaps: the top
// row (7/8/9) plus "+" stand in for choices A-D, positionally.
const NUMPAD_KEYS = ["A", "B", "C", "D"];
const NUMPAD_SPOKEN_NAMES = ["A", "B", "C", "D"];

// event.code is the physical key ("Numpad7") and is NumLock-independent;
// event.key is the character it produces ("7", but "Home" if NumLock is
// off). Match on code first, key as a fallback for keyboards/BT HID
// adapters that don't report numpad codes distinctly.
const NUMPAD_CODE_TO_INDEX: Record<string, number> = {
  Numpad7: 0,
  Numpad8: 1,
  Numpad9: 2,
  NumpadAdd: 3,
};
const NUMPAD_KEY_TO_INDEX: Record<string, number> = { "7": 0, "8": 1, "9": 2, "+": 3 };

function numpadIndexFor(event: KeyboardEvent): number | undefined {
  if (event.code in NUMPAD_CODE_TO_INDEX) return NUMPAD_CODE_TO_INDEX[event.code];
  if (event.key in NUMPAD_KEY_TO_INDEX) return NUMPAD_KEY_TO_INDEX[event.key];
  return undefined;
}

function announcementFor(question: LessonQuestion, options: Option[]) {
  const optionLines = options
    .map((option, index) => `Press ${NUMPAD_SPOKEN_NAMES[index]} for ${option.label}.`)
    .join(" ");
  return `${question.question_text} ${optionLines}`;
}

type QuestionCardProps = {
  question: LessonQuestion;
  interactive: boolean;
  narration: NarrationController;
  onSubmit: (selectedKey: string) => Promise<SubmitResponseResult>;
  onResult?: (result: SubmitResponseResult) => void;
};

export function QuestionCard({
  question,
  interactive,
  narration,
  onSubmit,
  onResult,
}: QuestionCardProps) {
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [result, setResult] = useState<SubmitResponseResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const options: Option[] = useMemo(
    () => (question.format === "TF" ? TF_OPTIONS : (question.choices ?? [])),
    [question.format, question.choices],
  );
  const answered = result !== null;

  // Announce the question and its numpad-key options as soon as it appears —
  // this card remounts fresh (keyed by question id) each time the learner
  // advances, so "on mount" lines up exactly with "new question is current."
  useEffect(() => {
    if (!interactive) return;
    narration.speak(announcementFor(question, options));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interactive, question.id]);

  const submitAnswer = useCallback(
    async (key: string) => {
      if (submitting || answered) return;
      setSelectedKey(key);
      setSubmitting(true);
      setError(null);
      try {
        const next = await onSubmit(key);
        setResult(next);
        onResult?.(next);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Couldn't submit your answer.");
      } finally {
        setSubmitting(false);
      }
    },
    [submitting, answered, onSubmit, onResult],
  );

  useEffect(() => {
    if (Platform.OS !== "web" || !interactive) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      const index = numpadIndexFor(event);
      if (index === undefined) return;
      const option = options[index];
      if (!option || submitting || answered) return;
      event.preventDefault();
      submitAnswer(option.key);
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [interactive, submitting, answered, options, submitAnswer]);

  return (
    <View style={styles.card}>
      <Text style={styles.tierLabel}>{TIER_LABELS[question.bloom_level] ?? question.bloom_level}</Text>
      <Text style={styles.questionText}>{question.question_text}</Text>

      <View style={styles.options}>
        {options.map((option, index) => {
          const isSelected = option.key === selectedKey;
          const isCorrectOption = option.key === question.correct_answer;
          const showAsCorrect = answered && isCorrectOption;
          const showAsWrong = answered && isSelected && !isCorrectOption;

          return (
            <Pressable
              key={option.key}
              disabled={!interactive || answered}
              onPress={() => submitAnswer(option.key)}
              style={[
                styles.option,
                isSelected && !answered && styles.optionSelected,
                showAsCorrect && styles.optionCorrect,
                showAsWrong && styles.optionWrong,
              ]}>
              <View style={styles.optionKeyBadge}>
                <Text style={styles.optionKeyText}>{NUMPAD_KEYS[index]}</Text>
              </View>
              <Text style={styles.optionText}>{option.label}</Text>
            </Pressable>
          );
        })}
      </View>

      {!interactive ? (
        <Text style={styles.lockedNote}>
          Reach this lesson in your learning path to answer its questions.
        </Text>
      ) : answered ? (
        <View style={styles.feedback}>
          <Text style={result?.is_correct ? styles.feedbackCorrect : styles.feedbackWrong}>
            {result?.is_correct ? "Correct!" : "Not quite — here's another to try."}
          </Text>
          {question.explanation ? (
            <Text style={styles.explanation}>{question.explanation}</Text>
          ) : null}
        </View>
      ) : submitting ? (
        <ActivityIndicator color={colors.maroon900} size="small" />
      ) : null}

      {error ? <Text style={styles.errorText}>{error}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.card,
    borderRadius: radii.control,
    borderWidth: 1,
    borderColor: colors.cardBorder,
    padding: spacing.md,
    gap: spacing.sm,
  },
  tierLabel: {
    fontFamily: fonts.bodyBold,
    fontSize: 11,
    letterSpacing: 1,
    textTransform: "uppercase",
    color: colors.inkSoft,
  },
  questionText: {
    fontFamily: fonts.displaySemi,
    fontSize: 16,
    color: colors.ink,
  },
  options: {
    gap: spacing.sm,
  },
  option: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: colors.cream,
    borderRadius: radii.control,
    borderWidth: 2,
    borderColor: colors.cardBorder,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  optionSelected: {
    borderColor: colors.selectedBorder,
  },
  optionCorrect: {
    borderColor: colors.correct,
    backgroundColor: colors.correctBg,
  },
  optionWrong: {
    borderColor: colors.incorrect,
    backgroundColor: colors.incorrectBg,
  },
  optionKeyBadge: {
    width: 24,
    height: 24,
    borderRadius: radii.pill,
    backgroundColor: colors.maroon900,
    alignItems: "center",
    justifyContent: "center",
  },
  optionKeyText: {
    fontFamily: fonts.bodyBold,
    fontSize: 11,
    color: colors.white,
  },
  optionText: {
    flex: 1,
    fontFamily: fonts.bodySemi,
    fontSize: 14,
    color: colors.ink,
  },
  feedback: {
    gap: spacing.xs,
  },
  feedbackCorrect: {
    fontFamily: fonts.bodyBold,
    fontSize: 14,
    color: colors.correct,
  },
  feedbackWrong: {
    fontFamily: fonts.bodyBold,
    fontSize: 14,
    color: colors.incorrect,
  },
  explanation: {
    fontFamily: fonts.body,
    fontSize: 13,
    lineHeight: 18,
    color: colors.inkSoft,
  },
  lockedNote: {
    fontFamily: fonts.body,
    fontSize: 12,
    fontStyle: "italic",
    color: colors.inkSoft,
  },
  errorText: {
    fontFamily: fonts.bodySemi,
    fontSize: 12,
    color: colors.incorrect,
  },
});
