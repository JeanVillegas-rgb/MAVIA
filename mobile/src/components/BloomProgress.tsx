import React from "react";
import { View, Text, StyleSheet } from "react-native";

import { BloomType } from "../models/LessonPackage";
import { colors, spacing } from "../theme/theme";

const STAGES: { key: BloomType; label: string }[] = [
  { key: "remember", label: "Remember" },
  { key: "understand", label: "Understand" },
  { key: "analyze", label: "Analyze" },
];

export default function BloomProgress({
  currentBloom,
}: {
  currentBloom: BloomType;
}) {
  const currentIndex = STAGES.findIndex((s) => s.key === currentBloom);

  return (
    <View
      style={styles.container}
      accessibilityRole="progressbar"
      accessibilityLabel={`Lesson stage ${currentIndex + 1} of ${STAGES.length}: ${STAGES[currentIndex]?.label ?? currentBloom}`}
    >
      {STAGES.map((stage, i) => {
        const done = i < currentIndex;
        const active = i === currentIndex;

        return (
          <React.Fragment key={stage.key}>
            <View style={styles.stage}>
              <View
                style={[
                  styles.dot,
                  done && styles.dotDone,
                  active && styles.dotActive,
                ]}
              >
                <Text style={styles.dotText}>{done ? "✓" : i + 1}</Text>
              </View>
              <Text
                style={[styles.stageLabel, active && styles.stageLabelActive]}
              >
                {stage.label}
              </Text>
            </View>
            {i < STAGES.length - 1 && (
              <View
                style={[styles.connector, done && styles.connectorDone]}
              />
            )}
          </React.Fragment>
        );
      })}
    </View>
  );
}

const DOT_SIZE = 28;

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "center",
    marginBottom: spacing.lg,
  },
  stage: {
    alignItems: "center",
    width: 84,
  },
  dot: {
    width: DOT_SIZE,
    height: DOT_SIZE,
    borderRadius: DOT_SIZE / 2,
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderColor: colors.border,
    justifyContent: "center",
    alignItems: "center",
  },
  dotDone: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
  },
  dotActive: {
    borderColor: colors.primary,
    borderWidth: 3,
  },
  dotText: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.textMuted,
  },
  stageLabel: {
    marginTop: spacing.xs,
    fontSize: 12,
    color: colors.textMuted,
    textAlign: "center",
  },
  stageLabelActive: {
    color: colors.primary,
    fontWeight: "700",
  },
  connector: {
    width: 20,
    height: 2,
    backgroundColor: colors.border,
    marginTop: DOT_SIZE / 2,
  },
  connectorDone: {
    backgroundColor: colors.accent,
  },
});