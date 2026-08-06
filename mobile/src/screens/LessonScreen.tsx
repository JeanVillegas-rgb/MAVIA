import React, { useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  AccessibilityInfo,
} from "react-native";
import * as Speech from "expo-speech";

import { useLessonContext } from "../context/LessonContext";
import PrimaryButton from "../components/PrimaryButton";
import BloomProgress from "../components/BloomProgress";
import { colors, spacing, typography, radii } from "../theme/theme";

export default function LessonScreen({ navigation }: any) {
  const {
    learningState,
    getCurrentNode,
    getCurrentBloom,
    getCurrentVariant,
    getCurrentMastery,
  } = useLessonContext();

  const currentNode = getCurrentNode();
  const currentBloom = getCurrentBloom();
  const currentVariant = getCurrentVariant();
  const mastery = getCurrentMastery();

  const previousNodeId = useRef<number | null>(null);
  const [justAdvanced, setJustAdvanced] = useState(false);

  const variant = currentNode?.variants?.[currentVariant] ?? currentNode?.variants?.normal;
  const nodeTitle = currentNode?.title ?? "";

  useEffect(() => {
    if (!currentNode || !variant) return;

    const advanced =
      previousNodeId.current !== null &&
      previousNodeId.current !== currentNode.id;

    setJustAdvanced(advanced);
    previousNodeId.current = currentNode.id;

    let cancelled = false;
    Speech.stop();

    const intro = advanced ? `Next lesson: ${nodeTitle}. ` : "";

    if (advanced) {
      AccessibilityInfo.announceForAccessibility(
        `Moving on to the next lesson: ${nodeTitle}`
      );
    }

    Speech.speak(`${intro}${variant.text}`, {
      rate: 0.95,
      onDone: () => {
        if (!cancelled) navigation.replace("Question");
      },
      onStopped: () => {},
      onError: () => {
        if (!cancelled) navigation.replace("Question");
      },
    });

    return () => {
      cancelled = true;
      Speech.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentVariant, currentBloom, currentNode, variant]);

  if (!learningState || !currentNode) {
    return (
      <View style={styles.center}>
        <Text style={typography.body}>No lesson loaded.</Text>
      </View>
    );
  }

  function replay() {
    if (!variant) return;
    Speech.stop();
    Speech.speak(variant.text, { rate: 0.95 });
  }

  if (!variant) {
    return (
      <View style={styles.center}>
        <Text style={typography.body}>
          This lesson variant isn't available yet.
        </Text>
      </View>
    );
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
          <Text style={styles.bannerText}>Next lesson</Text>
        </View>
      )}

      <Text style={typography.title}>{nodeTitle}</Text>

      <BloomProgress currentBloom={currentBloom} />

      <View style={styles.statsRow}>
        <View style={styles.statPill}>
          <Text style={styles.statLabel}>Mastery</Text>
          <Text style={styles.statValue}>{Math.round(mastery * 100)}%</Text>
        </View>
        <View style={styles.statPill}>
          <Text style={styles.statLabel}>Variant</Text>
          <Text style={styles.statValue}>
            {currentVariant.charAt(0).toUpperCase() + currentVariant.slice(1)}
          </Text>
        </View>
      </View>

      <View style={styles.card}>
        <Text style={typography.body}>{variant.text}</Text>
      </View>

      <Text style={[typography.bodyMuted, styles.playing]}>
        Playing lesson...
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
    flexWrap: "wrap",
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
    marginBottom: spacing.sm,
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
