import React, { useContext, useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  AccessibilityInfo,
} from "react-native";
import * as Speech from "expo-speech";

import { LessonContext } from "../context/LessonContext";
import PrimaryButton from "../components/PrimaryButton";
import BloomProgress from "../components/BloomProgress";
import { colors, spacing, typography, radii } from "../theme/theme";

export default function LessonScreen({ navigation }: any) {
  const {
    module,
    mastery,
    currentVariant,
    currentBloom,
    currentLessonIndex,
  } = useContext(LessonContext);

  const previousLessonId = useRef<number | null>(null);
  const [justAdvanced, setJustAdvanced] = useState(false);

  if (!module) {
    return (
      <View style={styles.center}>
        <Text style={typography.body}>No lesson package loaded.</Text>
      </View>
    );
  }

  const lesson = module.lesson_nodes[currentLessonIndex];

  if (!lesson) {
    return (
      <View style={styles.center}>
        <Text style={typography.body}>Lesson not found.</Text>
      </View>
    );
  }

  const variant =
    lesson.variants[currentVariant] ??
    lesson.variants.normal;

  const lessonTitle = lesson.title;

  useEffect(() => {
    if (!variant) return;

    const advanced =
      previousLessonId.current !== null &&
      previousLessonId.current !== lesson.id;

    setJustAdvanced(advanced);
    previousLessonId.current = lesson.id;

    let cancelled = false;

    Speech.stop();

    const intro = advanced
      ? `Next lesson. ${lessonTitle}. `
      : "";

    if (advanced) {
      AccessibilityInfo.announceForAccessibility(
        `Moving to the next lesson: ${lessonTitle}`
      );
    }

    Speech.speak(`${intro}${variant.text}`, {
      rate: 0.95,
      onDone: () => {
        if (!cancelled) {
          navigation.replace("Question");
        }
      },
      onError: () => {
        if (!cancelled) {
          navigation.replace("Question");
        }
      },
    });

    return () => {
      cancelled = true;
      Speech.stop();
    };
  }, [
    lesson.id,
    lessonTitle,
    variant,
    currentVariant,
    currentBloom,
    navigation,
  ]);

  function replay() {
    Speech.stop();

    Speech.speak(variant.text, {
      rate: 0.95,
    });
  }

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
    >
      {justAdvanced && (
        <View
          style={styles.banner}
          accessibilityLiveRegion="polite"
        >
          <Text style={styles.bannerText}>
            Next lesson
          </Text>
        </View>
      )}

      <Text style={typography.title}>
        {lessonTitle}
      </Text>

      <BloomProgress currentBloom={currentBloom} />

      <View style={styles.statsRow}>
        <View style={styles.statPill}>
          <Text style={styles.statLabel}>
            Mastery
          </Text>

          <Text style={styles.statValue}>
            {Math.round(mastery * 100)}%
          </Text>
        </View>

        <View style={styles.statPill}>
          <Text style={styles.statLabel}>
            Variant
          </Text>

          <Text style={styles.statValue}>
            {currentVariant.charAt(0).toUpperCase() +
              currentVariant.slice(1)}
          </Text>
        </View>
      </View>

      <View style={styles.card}>
        <Text style={typography.body}>
          {variant.text}
        </Text>
      </View>

      <Text
        style={[
          typography.bodyMuted,
          styles.playing,
        ]}
      >
        Playing lesson…
      </Text>

      <PrimaryButton
        label="Replay lesson"
        onPress={replay}
        variant="outline"
        accessibilityHint="Plays the lesson narration again from the start"
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },

  content: {
    padding: spacing.lg,
    paddingBottom: spacing.xxl,
  },

  center: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: colors.background,
  },

  banner: {
    alignSelf: "flex-start",
    backgroundColor: colors.accentMuted,
    borderRadius: radii.pill,
    paddingVertical: spacing.xs,
    paddingHorizontal: spacing.md,
    marginBottom: spacing.md,
  },

  bannerText: {
    color: colors.accent,
    fontWeight: "700",
    fontSize: 13,
  },

  statsRow: {
    flexDirection: "row",
    marginBottom: spacing.lg,
  },

  statPill: {
    backgroundColor: colors.surface,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
    marginRight: spacing.sm,
  },

  statLabel: {
    ...typography.label,
    marginBottom: 2,
  },

  statValue: {
    fontSize: 18,
    fontWeight: "700",
    color: colors.textPrimary,
  },

  card: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    marginBottom: spacing.md,
  },

  playing: {
    marginBottom: spacing.lg,
    fontStyle: "italic",
  },
});
