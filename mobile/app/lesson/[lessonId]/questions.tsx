import React, { useEffect, useMemo, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import TopNav from "@/components/TopNav";
import PrimaryButton from "@/components/PrimaryButton";
import ProgressBar from "@/components/ProgressBar";
import QuestionOption from "@/components/QuestionOption";
import { fetchLessonPackage, submitAnswer } from "@/data/api";
import { AnsweredQuestion, LessonPackage } from "@/data/types";
import { colors, fonts, radii, spacing } from "@/theme";

const LEARNER_ID = "demo-learner"; // stand-in until auth/learner context exists

export default function Questions() {
  const { lessonId } = useLocalSearchParams<{ lessonId: string }>();
  const router = useRouter();

  const [lesson, setLesson] = useState<LessonPackage | null>(null);
  const [qIndex, setQIndex] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [answers, setAnswers] = useState<AnsweredQuestion[]>([]);
  const [startedAt, setStartedAt] = useState(Date.now());

  useEffect(() => {
    if (!lessonId) return;
    fetchLessonPackage(lessonId).then(setLesson);
  }, [lessonId]);

  const question = lesson?.questions[qIndex];

  const options = useMemo(() => {
    if (!question) return [];
    if (question.format === "TF") {
      return [
        { key: "true", label: "True" },
        { key: "false", label: "False" },
      ];
    }
    return question.choices ?? [];
  }, [question]);

  if (!lesson || !question) {
    return (
      <View style={styles.screen}>
        <TopNav showBack title="Loading…" />
      </View>
    );
  }

  const isLast = qIndex === lesson.questions.length - 1;

  const handleSelect = (key: string) => {
    if (revealed) return;
    setSelected(key);
  };

  const handleCheck = async () => {
    if (!selected) return;
    const isCorrect = selected === question.correctAnswer;
    setRevealed(true);
    setAnswers((a) => [...a, { questionId: question.id, selected, isCorrect }]);

    await submitAnswer({
      learnerId: LEARNER_ID,
      questionId: question.id,
      selectedAnswer: selected,
      isCorrect,
      responseTimeSec: (Date.now() - startedAt) / 1000,
    });
  };

  const handleContinue = () => {
    if (isLast) {
      const finalAnswers = answers;
      const correctCount = finalAnswers.filter((a) => a.isCorrect).length;
      router.push({
        pathname: `/lesson/${lessonId}/results`,
        params: { correct: String(correctCount), total: String(lesson.questions.length) },
      });
      return;
    }
    setQIndex((i) => i + 1);
    setSelected(null);
    setRevealed(false);
    setStartedAt(Date.now());
  };

  const getOptionState = (key: string) => {
    if (!revealed) return selected === key ? "selected" : "idle";
    if (key === question.correctAnswer) return "correct";
    if (key === selected) return "incorrect";
    return "idle";
  };

  return (
    <View style={styles.screen}>
      <TopNav showBack title={lesson.title} />

      <View style={styles.progressWrap}>
        <ProgressBar progress={(qIndex + 1) / lesson.questions.length} />
        <Text style={styles.progressLabel}>
          Question {qIndex + 1} of {lesson.questions.length}
        </Text>
      </View>

      <View style={styles.card}>
        <View style={styles.badgeRow}>
          <Text style={styles.badge}>{question.bloomLevel}</Text>
          <Text style={styles.badge}>{question.difficulty}</Text>
        </View>

        <Text style={styles.questionText}>{question.questionText}</Text>

        <View style={styles.options}>
          {options.map((opt) => (
            <QuestionOption
              key={opt.key}
              label={opt.label}
              state={getOptionState(opt.key)}
              disabled={revealed}
              onPress={() => handleSelect(opt.key)}
            />
          ))}
        </View>

        {revealed && (
          <View style={styles.explanationBox}>
            <Text style={styles.explanationText}>{question.explanation}</Text>
          </View>
        )}
      </View>

      <View style={styles.footer}>
        {!revealed ? (
          <PrimaryButton label="Check Answer" onPress={handleCheck} disabled={!selected} />
        ) : (
          <PrimaryButton label={isLast ? "See Results" : "Next Question"} onPress={handleContinue} />
        )}
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
  progressWrap: {
    gap: spacing.xs,
  },
  progressLabel: {
    fontFamily: fonts.body,
    fontSize: 12,
    color: colors.inkSoft,
  },
  card: {
    flex: 1,
    backgroundColor: colors.card,
    borderRadius: radii.card,
    padding: spacing.lg,
    gap: spacing.md,
  },
  badgeRow: {
    flexDirection: "row",
    gap: spacing.sm,
  },
  badge: {
    fontFamily: fonts.bodyBold,
    fontSize: 10,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: colors.maroon900,
    backgroundColor: colors.blush,
    paddingVertical: 4,
    paddingHorizontal: 10,
    borderRadius: radii.pill,
    overflow: "hidden",
  },
  questionText: {
    fontFamily: fonts.displaySemi,
    fontSize: 19,
    color: colors.ink,
    lineHeight: 26,
  },
  options: {
    marginTop: spacing.sm,
  },
  explanationBox: {
    backgroundColor: colors.white,
    borderRadius: radii.control,
    padding: spacing.md,
  },
  explanationText: {
    fontFamily: fonts.body,
    fontSize: 13,
    color: colors.inkSoft,
    lineHeight: 20,
  },
  footer: {
    paddingBottom: spacing.md,
  },
});
