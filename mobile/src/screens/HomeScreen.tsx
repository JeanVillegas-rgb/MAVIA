import React, { useState } from "react";
import { View, Text, StyleSheet, SafeAreaView } from "react-native";
import { useLessonContext } from "../context/LessonContext";
import { startLearning } from "../services/lessonService";
import PrimaryButton from "../components/PrimaryButton";
import { colors, spacing, typography, radii } from "../theme/theme";

export default function HomeScreen({ navigation }: any) {
  const { setLearningState, setCurrentQuestionIndex } = useLessonContext();

  const [loading, setLoading] = useState(false);

  async function startLesson() {
    try {
      setLoading(true);
      const result = await startLearning();
      setLearningState(result);
      setCurrentQuestionIndex(0);
      navigation.replace("Lesson");
    } catch (err) {
      console.log(err);
    } finally {
      setLoading(false);
    }
  }

  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.container}>
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
          <Text style={styles.moduleTitle}>Adaptive Module</Text>
        </View>
        <PrimaryButton
          label="Start lesson"
          onPress={startLesson}
          loading={loading}
          accessibilityHint="Begins the adaptive lesson"
        />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: colors.background,
  },
  container: {
    flex: 1,
    justifyContent: "center",
    paddingHorizontal: spacing.xl,
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
});