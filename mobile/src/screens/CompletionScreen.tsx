import React, { useContext, useEffect } from "react";
import { View, Text, StyleSheet, AccessibilityInfo } from "react-native";
import * as Speech from "expo-speech";

import { LessonContext } from "../context/LessonContext";
import PrimaryButton from "../components/PrimaryButton";
import { colors, spacing, typography, radii } from "../theme/theme";

export default function CompletionScreen({ navigation }: any) {
  const { mastery } = useContext(LessonContext);
  const finalMastery = Math.round(mastery * 100);

  useEffect(() => {
    const message = `Module complete. Your final mastery is ${finalMastery} percent. Great work.`;
    AccessibilityInfo.announceForAccessibility(message);
    Speech.stop();
    Speech.speak(message, { rate: 0.95 });

    return () => {
      Speech.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <View style={styles.container}>
      <View style={styles.badge}>
        <Text style={styles.badgeText}>✓</Text>
      </View>

      <Text style={typography.title} accessibilityRole="header">
        Module complete
      </Text>

      <Text style={[typography.bodyMuted, styles.subtitle]}>
        You've worked through every lesson in this module.
      </Text>

      <View style={styles.masteryCard}>
        <Text style={typography.label}>Final mastery</Text>
        <Text style={styles.masteryValue}>{finalMastery}%</Text>
      </View>

      <Text style={[typography.body, styles.encouragement]}>
        Great job — you've completed this adaptive lesson module.
      </Text>

      <PrimaryButton
        label="Back to home"
        onPress={() => navigation.replace("Home")}
        accessibilityHint="Returns to the home screen"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
    justifyContent: "center",
    alignItems: "center",
    padding: spacing.xl,
  },
  badge: {
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: colors.accent,
    justifyContent: "center",
    alignItems: "center",
    marginBottom: spacing.lg,
  },
  badgeText: {
    color: "#fff",
    fontSize: 32,
    fontWeight: "700",
  },
  subtitle: {
    textAlign: "center",
    marginTop: spacing.sm,
    marginBottom: spacing.lg,
  },
  masteryCard: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    paddingVertical: spacing.lg,
    paddingHorizontal: spacing.xxl,
    alignItems: "center",
    marginBottom: spacing.lg,
  },
  masteryValue: {
    fontSize: 48,
    fontWeight: "700",
    color: colors.primary,
    marginTop: spacing.xs,
  },
  encouragement: {
    textAlign: "center",
    marginBottom: spacing.xl,
  },
});