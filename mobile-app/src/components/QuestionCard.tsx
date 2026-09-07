import React, { useMemo, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

import Button from "@/components/Button";
import { colors, radii, spacing } from "@/theme";

// Mirrors lessons.Question from the API.
export type Question = {
  id: number;
  order: number;
  prompt: string;
  question_type: "open_ended" | "true_false" | "multiple_choice" | string;
  choices: string[];
  correct_answer: string;
};

export type SubmitResult = { is_correct: boolean; mastery: number; completed: boolean };

type Option = { key: string; label: string };
const LETTERS = ["a", "b", "c", "d", "e", "f"];

function optionsFor(question: Question): Option[] {
  if (question.question_type === "true_false") {
    return [
      { key: "true", label: "True" },
      { key: "false", label: "False" },
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
};

export default function QuestionCard({ question, index, total, onSubmit, onNext }: Props) {
  const options = useMemo(() => optionsFor(question), [question]);
  const [selected, setSelected] = useState<string | null>(null);
  const [result, setResult] = useState<SubmitResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const answered = result !== null;
  const openEnded = options.length === 0;

  async function choose(key: string) {
    if (submitting || answered) return;
    setSelected(key);
    setSubmitting(true);
    setError(null);
    try {
      setResult(await onSubmit(key));
    } catch (err) {
      setSelected(null);
      setError(err instanceof Error ? err.message : "Couldn't submit your answer.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <View style={styles.card}>
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
            return (
              <Pressable
                key={option.key}
                disabled={answered || submitting}
                onPress={() => choose(option.key)}
                accessibilityRole="button"
                style={[
                  styles.option,
                  option.key === selected && !answered && styles.optionSelected,
                  isCorrect && styles.optionCorrect,
                  isWrongPick && styles.optionWrong,
                ]}
              >
                <View style={styles.optionKey}>
                  <Text style={styles.optionKeyText}>{option.key.toUpperCase()}</Text>
                </View>
                <Text style={styles.optionLabel}>{option.label}</Text>
              </Pressable>
            );
          })}
        </View>
      )}

      {submitting && <ActivityIndicator color={colors.brand600} />}
      {error && <Text style={styles.error}>{error}</Text>}

      {answered && (
        <View style={styles.feedback}>
          <Text style={result?.is_correct ? styles.correct : styles.wrong}>
            {result?.is_correct ? "Correct" : "Not quite"}
          </Text>
        </View>
      )}

      {(answered || openEnded) && (
        <Button
          label={index + 1 < total ? "Next question" : "Finish lesson"}
          onPress={onNext}
          style={{ marginTop: spacing.sm }}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    gap: spacing.sm,
  },
  counter: {
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 1,
    textTransform: "uppercase",
    color: colors.faint,
  },
  prompt: { fontSize: 16, fontWeight: "700", color: colors.ink },
  note: { fontSize: 13, color: colors.muted, fontStyle: "italic" },
  options: { gap: spacing.sm },
  option: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: colors.panel,
    borderRadius: radii.sm,
    borderWidth: 2,
    borderColor: colors.border,
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
  feedback: { gap: spacing.xs },
  correct: { fontSize: 14, fontWeight: "800", color: colors.success },
  wrong: { fontSize: 14, fontWeight: "800", color: colors.danger },
  error: { fontSize: 12, fontWeight: "600", color: colors.danger },
});
