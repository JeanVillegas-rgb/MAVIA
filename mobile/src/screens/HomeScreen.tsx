import React, { useContext, useState } from "react";
import { View, Text, StyleSheet, SafeAreaView, ScrollView } from "react-native";

import { LessonContext } from "../context/LessonContext";
import { startLearning } from "../services/lessonService";
import PrimaryButton from "../components/PrimaryButton";
import { colors, spacing, typography, radii } from "../theme/theme";

export default function HomeScreen({ navigation }: any) {
  const {
    setLesson,
    setMastery,
    setCurrentVariant,
    setCurrentBloom,
    setLearningStateId,
    setCurrentNodeId,
    setCurrentQuestionIndex
  } =
    useContext(LessonContext);

  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  async function startLesson() {
    try {
      setLoading(true);
      setErrorMessage("");

      const session = await startLearning();

      setLesson(session.lesson);
      setMastery(session.mastery);
      setCurrentVariant(session.current_variant);
      setCurrentBloom(session.current_bloom);
      setLearningStateId(session.learning_state_id);
      setCurrentNodeId(session.current_node_id);
      setCurrentQuestionIndex(0);

      navigation.navigate("Lesson");
    } catch (err) {
      console.log(err);
      const detail =
        (err as any)?.response?.data?.error ||
        (err as any)?.response?.data?.detail ||
        (err as any)?.message ||
        "Make sure the backend is running and questions are generated.";
      setErrorMessage(
        `Unable to start lesson. ${detail}`
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <SafeAreaView style={styles.safe}>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.container}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.hero}>
          <Text style={styles.wordmark} accessibilityRole="header">
            MAVIA
          </Text>
          <Text style={[typography.bodyMuted, styles.tagline]}>
            Adaptive learning, built for how you learn best.
          </Text>
        </View>

        <View style={styles.moduleCard}>
          <Text style={typography.label}>Up next</Text>
          <Text style={styles.moduleTitle}>Module 1</Text>
        </View>

        {errorMessage ? (
          <View style={styles.errorCard}>
            <Text style={styles.errorText}>{errorMessage}</Text>
          </View>
        ) : null}

        <PrimaryButton
          label="Start lesson"
          onPress={startLesson}
          loading={loading}
          accessibilityHint="Begins the next lesson in this module"
        />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: colors.background,
  },
  scroll: {
    flex: 1,
  },
  container: {
    flexGrow: 1,
    justifyContent: "center",
    paddingHorizontal: spacing.xl,
    paddingVertical: spacing.xxl,
    alignSelf: "center",
    width: "100%",
    maxWidth: 520,
  },
  hero: {
    alignItems: "center",
    marginBottom: spacing.xxl,
  },
  wordmark: {
    fontSize: 44,
    fontWeight: "800",
    letterSpacing: 1,
    color: colors.primary,
  },
  tagline: {
    marginTop: spacing.sm,
    textAlign: "center",
  },
  moduleCard: {
    backgroundColor: colors.surface,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    marginBottom: spacing.xl,
  },
  moduleTitle: {
    fontSize: 22,
    fontWeight: "700",
    color: colors.textPrimary,
    marginTop: spacing.xs,
  },
  errorCard: {
    backgroundColor: colors.retryMuted,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.retry,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  errorText: {
    ...typography.bodyMuted,
    color: colors.retry,
  },
});
