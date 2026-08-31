import { SymbolView } from "expo-symbols";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, useWindowDimensions, View } from "react-native";

import { getLearningState, getLessonPackage, submitResponse } from "@/api/client";
import type {
  LearningStateRecord,
  LessonChunk,
  LessonPackage,
  LessonVariantKey,
  SubmitResponseResult,
} from "@/api/types";
import { NarrativeCard } from "@/components/ui/narrative-card";
import { ProgressBar } from "@/components/ui/progress-bar";
import { QuestionCard } from "@/components/ui/question-card";
import { ScreenFrame } from "@/components/ui/screen-frame";
import { useNarration } from "@/hooks/use-narration";
import { colors, fonts, radii, spacing } from "@/theme";

const VARIANT_LABELS: Record<LessonVariantKey, string> = {
  normal: "Standard explanation",
  elaborated: "Elaborated explanation",
  simplified: "Simplified explanation",
};

const WIDE_BREAKPOINT = 700;

function preferredVariantText(chunk: LessonChunk, preferred: LessonVariantKey) {
  return chunk.variants[preferred] ?? chunk.variants.normal ?? Object.values(chunk.variants)[0];
}

function chunkIndexForId(lesson: LessonPackage, chunkId: number | null | undefined) {
  if (chunkId == null) return -1;
  return lesson.chunks.findIndex((chunk) => chunk.id === chunkId);
}

export default function LessonDetailScreen() {
  const { id, learningStateId } = useLocalSearchParams<{ id: string; learningStateId?: string }>();
  // Remount on param change instead of manually resetting state in an effect —
  // expo-router reuses this component instance across /lesson/[id] navigations.
  return <LessonDetailContent key={`${id}:${learningStateId ?? ""}`} id={id} learningStateId={learningStateId} />;
}

function LessonDetailContent({ id, learningStateId }: { id: string; learningStateId?: string }) {
  const router = useRouter();
  const { width } = useWindowDimensions();
  const isWide = width >= WIDE_BREAKPOINT;

  const nodeId = Number(id);
  const interactive = Boolean(learningStateId);

  const [lesson, setLesson] = useState<LessonPackage | null>(null);
  const [learningState, setLearningState] = useState<LearningStateRecord | null>(null);
  const [activeChunkIndex, setActiveChunkIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [completionMessage, setCompletionMessage] = useState<string | null>(null);
  const [nextNodeId, setNextNodeId] = useState<number | null>(null);
  // A thin question pool can resolve "next question" back to the same id
  // (nothing else left to serve) — this forces QuestionCard to remount even
  // when the id repeats, instead of freezing on stale "answered" state.
  const [attemptCount, setAttemptCount] = useState(0);
  // Which chunk's narration has already been read aloud, so a learner hears
  // the lesson content before its questions — once per chunk, not once per
  // question (tier advances within the same chunk skip straight to the
  // question; only a genuinely new chunk gets introduced first).
  const [introducedChunkId, setIntroducedChunkId] = useState<number | null>(null);
  const narration = useNarration();

  useEffect(() => {
    let cancelled = false;

    // Sequential: getLessonPackage writes (syncs module questions), so it
    // shouldn't race a concurrent request against the same SQLite database.
    getLessonPackage(nodeId)
      .then((lessonResult) =>
        (learningStateId ? getLearningState(Number(learningStateId)) : Promise.resolve(null)).then(
          (stateResult) => {
            if (cancelled) return;
            setLesson(lessonResult);
            setLearningState(stateResult);
            const startChunkIndex = chunkIndexForId(
              lessonResult,
              lessonResult.questions.find((q) => q.id === stateResult?.current_question)?.chunk_id,
            );
            if (startChunkIndex !== -1) setActiveChunkIndex(startChunkIndex);
          },
        ),
      )
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Couldn't load this lesson.");
      });

    return () => {
      cancelled = true;
    };
  }, [nodeId, learningStateId]);

  const preferredVariant: LessonVariantKey = interactive
    ? ((learningState?.current_variant.toLowerCase() ?? "normal") as LessonVariantKey)
    : "normal";
  const activeChunk = lesson?.chunks[activeChunkIndex] ?? lesson?.chunks[0];
  const excerpt = activeChunk ? preferredVariantText(activeChunk, preferredVariant) : undefined;
  const currentQuestion = interactive
    ? lesson?.questions.find((q) => q.id === learningState?.current_question)
    : undefined;
  const questionChunk = currentQuestion
    ? lesson?.chunks.find((chunk) => chunk.id === currentQuestion.chunk_id)
    : undefined;
  const questionChunkText = questionChunk
    ? preferredVariantText(questionChunk, preferredVariant)?.text
    : undefined;
  const chunkIntroduced =
    !questionChunk || !questionChunkText || questionChunk.id === introducedChunkId;

  // Read the chunk's own narration before its questions — once per chunk,
  // keyed off the question's chunk (not whatever the learner happens to be
  // manually browsing), so tier advances within the same chunk don't repeat it.
  useEffect(() => {
    if (!interactive || !questionChunk || !questionChunkText || questionChunk.id === introducedChunkId) {
      return;
    }
    narration.speak(questionChunkText, { onDone: () => setIntroducedChunkId(questionChunk.id) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interactive, questionChunk?.id]);

  if (error) {
    return (
      <ScreenFrame eyebrow="Modules">
        <Text style={styles.errorText}>{error}</Text>
        <Pressable onPress={() => router.back()} style={styles.backRow}>
          <Text style={styles.backText}>Back to lessons</Text>
        </Pressable>
      </ScreenFrame>
    );
  }

  if (!lesson) {
    return (
      <ScreenFrame eyebrow="Modules">
        <View style={styles.loadingWrap}>
          <ActivityIndicator color={colors.maroon900} size="large" />
        </View>
      </ScreenFrame>
    );
  }

  const handleSubmitAnswer = async (questionId: number, selectedKey: string) => {
    return submitResponse({
      learningStateId: Number(learningStateId),
      questionId,
      selectedAnswer: selectedKey,
    });
  };

  const applyAdvance = (result: SubmitResponseResult) => {
    setLearningState((prev) => (prev ? { ...prev, current_question: result.next_question } : prev));
    setAttemptCount((count) => count + 1);

    if (result.completed) {
      setCompletionMessage("You've completed the course! Great work.");
      return;
    }
    if (result.node_changed) {
      setCompletionMessage("Lesson mastered! Ready for the next one.");
      setNextNodeId(result.next_node);
      return;
    }
    if (result.chunk_changed) {
      const chunkIndex = chunkIndexForId(lesson, result.next_chunk);
      if (chunkIndex !== -1) setActiveChunkIndex(chunkIndex);
    }
  };

  const handleResult = (result: SubmitResponseResult) => {
    setLearningState((prev) =>
      prev
        ? { ...prev, mastery: result.mastery, current_variant: result.next_variant, current_bloom: result.next_bloom }
        : prev,
    );

    // Speak the feedback (and, on a wrong answer, the escalated explanation
    // for this chunk) and only advance once the learner has actually heard
    // it — no button to press either way.
    const feedback = [result.is_correct ? "Correct." : "Not quite."];
    if (currentQuestion?.explanation) feedback.push(currentQuestion.explanation);

    if (!result.is_correct && currentQuestion) {
      const chunkIndex = chunkIndexForId(lesson, currentQuestion.chunk_id);
      if (chunkIndex !== -1) {
        setActiveChunkIndex(chunkIndex);
        const nextVariant = result.next_variant.toLowerCase() as LessonVariantKey;
        const revised = preferredVariantText(lesson.chunks[chunkIndex], nextVariant);
        if (revised?.text) feedback.push(revised.text);
      }
    }

    narration.speak(feedback.join(" "), { onDone: () => applyAdvance(result) });
  };

  return (
    <ScreenFrame eyebrow={lesson.module.title}>
      <Pressable style={styles.backRow} onPress={() => router.back()} hitSlop={8}>
        <SymbolView
          name={{ ios: "chevron.left", android: "arrow_back", web: "arrow_back" }}
          tintColor={colors.inkSoft}
          size={16}
        />
        <Text style={styles.backText}>Back to lessons</Text>
      </Pressable>

      <View style={[styles.layout, isWide && styles.layoutWide]}>
        <View style={isWide ? styles.narrativeWide : styles.narrative}>
          <NarrativeCard
            title={`${lesson.lesson_node.title}:`}
            large
            selected={interactive}
            playing={Boolean(excerpt) && narration.speakingText === excerpt?.text}
            onTogglePlay={excerpt?.text ? () => narration.toggle(excerpt.text) : undefined}
          />
        </View>

        <View style={isWide ? styles.sidebarWide : styles.sidebar}>
          {interactive ? <ProgressBar progress={learningState?.mastery ?? 0} /> : null}
          {activeChunk ? (
            <View style={styles.excerptCard}>
              <Text style={styles.excerptCaption}>{activeChunk.title}</Text>
              {interactive ? (
                <Text style={styles.variantLabel}>{VARIANT_LABELS[preferredVariant]}</Text>
              ) : null}
              <Text style={styles.excerptText}>{excerpt?.text ?? "No content yet."}</Text>
            </View>
          ) : null}

          {lesson.chunks.map((chunk, index) => (
            <Pressable
              key={chunk.id}
              onPress={() => {
                narration.stop();
                setActiveChunkIndex(index);
              }}
              style={[styles.chunkRow, index === activeChunkIndex && styles.chunkRowActive]}>
              <Text style={styles.chunkRowText} numberOfLines={1}>
                {chunk.title}
              </Text>
            </Pressable>
          ))}
        </View>
      </View>

      {completionMessage ? (
        <View style={styles.completionBanner}>
          <Text style={styles.completionText}>{completionMessage}</Text>
          {nextNodeId ? (
            <Pressable
              style={styles.nextLessonButton}
              onPress={() =>
                router.replace({
                  pathname: "/lesson/[id]",
                  params: { id: String(nextNodeId), learningStateId },
                })
              }>
              <Text style={styles.nextLessonButtonText}>Continue</Text>
            </Pressable>
          ) : null}
        </View>
      ) : interactive && currentQuestion && !chunkIntroduced ? (
        <View style={styles.readingWrap}>
          <ActivityIndicator color={colors.maroon900} size="small" />
          <Text style={styles.readingText}>Reading the lesson content…</Text>
        </View>
      ) : interactive && currentQuestion ? (
        <QuestionCard
          key={`${currentQuestion.id}:${attemptCount}`}
          question={currentQuestion}
          interactive={interactive}
          narration={narration}
          onSubmit={(selectedKey) => handleSubmitAnswer(currentQuestion.id, selectedKey)}
          onResult={handleResult}
        />
      ) : interactive ? (
        <Text style={styles.noQuestionsText}>No questions available for this topic yet.</Text>
      ) : null}
    </ScreenFrame>
  );
}

const styles = StyleSheet.create({
  backRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.xs,
  },
  backText: {
    fontFamily: fonts.bodySemi,
    fontSize: 13,
    color: colors.inkSoft,
  },
  loadingWrap: {
    paddingVertical: spacing.xl,
    alignItems: "center",
  },
  errorText: {
    fontFamily: fonts.bodySemi,
    fontSize: 14,
    color: colors.incorrect,
  },
  layout: {
    gap: spacing.md,
  },
  layoutWide: {
    flexDirection: "row",
    alignItems: "flex-start",
  },
  narrative: {
    width: "100%",
  },
  narrativeWide: {
    flex: 3,
  },
  sidebar: {
    width: "100%",
    gap: spacing.sm,
  },
  sidebarWide: {
    flex: 2,
    gap: spacing.sm,
  },
  excerptCard: {
    backgroundColor: colors.blush,
    borderRadius: radii.control,
    padding: spacing.md,
    gap: spacing.sm,
  },
  excerptCaption: {
    fontFamily: fonts.bodyBold,
    fontSize: 12,
    color: colors.inkSoft,
  },
  variantLabel: {
    fontFamily: fonts.bodySemi,
    fontSize: 11,
    letterSpacing: 0.5,
    textTransform: "uppercase",
    color: colors.gold,
  },
  excerptText: {
    fontFamily: fonts.body,
    fontSize: 14,
    lineHeight: 20,
    color: colors.ink,
  },
  chunkRow: {
    backgroundColor: colors.card,
    borderRadius: radii.control,
    borderWidth: 1,
    borderColor: colors.cardBorder,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
  },
  chunkRowActive: {
    borderColor: colors.selectedBorder,
    borderWidth: 2,
  },
  chunkRowText: {
    fontFamily: fonts.bodyBold,
    fontSize: 14,
    color: colors.ink,
  },
  completionBanner: {
    backgroundColor: colors.correctBg,
    borderRadius: radii.control,
    padding: spacing.md,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: spacing.sm,
  },
  completionText: {
    flex: 1,
    fontFamily: fonts.bodyBold,
    fontSize: 14,
    color: colors.correct,
  },
  nextLessonButton: {
    backgroundColor: colors.maroon900,
    borderRadius: radii.pill,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  nextLessonButtonText: {
    fontFamily: fonts.bodyBold,
    fontSize: 13,
    color: colors.white,
  },
  noQuestionsText: {
    fontFamily: fonts.body,
    fontSize: 13,
    fontStyle: "italic",
    color: colors.inkSoft,
  },
  readingWrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    padding: spacing.md,
  },
  readingText: {
    fontFamily: fonts.body,
    fontSize: 13,
    fontStyle: "italic",
    color: colors.inkSoft,
  },
});
