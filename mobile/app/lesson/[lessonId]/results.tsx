import React from "react";
import { StyleSheet, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import TopNav from "@/components/TopNav";
import PrimaryButton from "@/components/PrimaryButton";
import { colors, fonts, radii, spacing } from "@/theme";

function feedbackFor(pct: number): { title: string; body: string; icon: keyof typeof Ionicons.glyphMap } {
  if (pct >= 0.8) {
    return {
      title: "Great job!",
      body: "Strong understanding of this lesson. Next lesson will move a little faster.",
      icon: "trophy",
    };
  }
  if (pct >= 0.5) {
    return {
      title: "Good effort",
      body: "You've got the basics — a couple of ideas are worth another look.",
      icon: "ribbon",
    };
  }
  return {
    title: "Let's try that again",
    body: "This lesson will be retaught using the Simplified version to help it click.",
    icon: "refresh",
  };
}

export default function Results() {
  const { lessonId, correct, total } = useLocalSearchParams<{
    lessonId: string;
    correct: string;
    total: string;
  }>();
  const router = useRouter();

  const correctNum = Number(correct ?? 0);
  const totalNum = Number(total ?? 1);
  const pct = totalNum > 0 ? correctNum / totalNum : 0;
  const feedback = feedbackFor(pct);

  return (
    <View style={styles.screen}>
      <TopNav showBack title="Results" />

      <View style={styles.card}>
        <View style={styles.scoreCircle}>
          <Text style={styles.scoreText}>
            {correctNum}/{totalNum}
          </Text>
        </View>

        <Ionicons name={feedback.icon} size={32} color={colors.maroon900} />
        <Text style={styles.title}>{feedback.title}</Text>
        <Text style={styles.body}>{feedback.body}</Text>
      </View>

      <View style={styles.footer}>
        <PrimaryButton label="Back to Lessons" onPress={() => router.push("/")} />
        <View style={{ height: spacing.sm }} />
        <PrimaryButton
          label="Retry Lesson"
          variant="outline"
          onPress={() => router.push(`/lesson/${lessonId}`)}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.cream,
    padding: spacing.md,
    gap: spacing.md,
  },
  card: {
    flex: 1,
    backgroundColor: colors.card,
    borderRadius: radii.card,
    padding: spacing.xl,
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
  },
  scoreCircle: {
    width: 120,
    height: 120,
    borderRadius: radii.pill,
    backgroundColor: colors.white,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: spacing.md,
    borderWidth: 4,
    borderColor: colors.maroon900,
  },
  scoreText: {
    fontFamily: fonts.display,
    fontSize: 24,
    color: colors.ink,
  },
  title: {
    fontFamily: fonts.display,
    fontSize: 22,
    color: colors.ink,
  },
  body: {
    fontFamily: fonts.body,
    fontSize: 14,
    color: colors.inkSoft,
    textAlign: "center",
    lineHeight: 20,
  },
  footer: {
    paddingBottom: spacing.md,
  },
});
