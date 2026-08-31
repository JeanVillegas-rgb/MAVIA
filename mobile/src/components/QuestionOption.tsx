import React from "react";
import { Pressable, StyleSheet, Text } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors, fonts, radii, spacing } from "@/theme";

type Props = {
  label: string;
  onPress: () => void;
  state: "idle" | "selected" | "correct" | "incorrect" | "reveal-correct";
  disabled?: boolean;
};

export default function QuestionOption({ label, onPress, state, disabled }: Props) {
  const style = STATE_STYLES[state];
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={[styles.base, style.container]}
    >
      <Text style={[styles.label, style.label]}>{label}</Text>
      {state === "correct" && <Ionicons name="checkmark-circle" size={20} color={colors.correct} />}
      {state === "incorrect" && <Ionicons name="close-circle" size={20} color={colors.incorrect} />}
      {state === "reveal-correct" && (
        <Ionicons name="checkmark-circle-outline" size={20} color={colors.correct} />
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 14,
    paddingHorizontal: spacing.md,
    borderRadius: radii.control,
    borderWidth: 1.5,
    marginBottom: spacing.sm,
  },
  label: {
    fontFamily: fonts.bodySemi,
    fontSize: 14,
    flexShrink: 1,
    paddingRight: spacing.sm,
  },
});

const STATE_STYLES: Record<
  Props["state"],
  { container: object; label: object }
> = {
  idle: {
    container: { backgroundColor: colors.card, borderColor: colors.cardBorder },
    label: { color: colors.ink },
  },
  selected: {
    container: { backgroundColor: colors.blush, borderColor: colors.maroon900 },
    label: { color: colors.maroon900 },
  },
  correct: {
    container: { backgroundColor: colors.correctBg, borderColor: colors.correct },
    label: { color: colors.correct },
  },
  incorrect: {
    container: { backgroundColor: colors.incorrectBg, borderColor: colors.incorrect },
    label: { color: colors.incorrect },
  },
  "reveal-correct": {
    container: { backgroundColor: colors.correctBg, borderColor: colors.correct },
    label: { color: colors.correct },
  },
};
