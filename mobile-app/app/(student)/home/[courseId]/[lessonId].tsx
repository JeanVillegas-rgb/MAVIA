import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";

import IconButton from "@/components/IconButton";
import GradientTile from "@/components/GradientTile";
import Button from "@/components/Button";
import QuestionCard, { Question as LegacyQuestion, SubmitResult } from "@/components/QuestionCard";
import { useAudioPlayer } from "@/hooks/useAudioPlayer";
import {
  ApiLesson,
  ApiStep,
  ApiSubmitResult,
  ApiTrack,
  Variant,
  fetchLessonPackage,
  resolveMediaUrl,
  startLearning,
  submitResponse,
} from "@/api/client";
import { colors, radii, spacing } from "@/theme";

function fmt(millis: number) {
  if (!millis || millis < 0) return "0:00";
  const total = Math.floor(millis / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

type Phase = "audio" | "questions" | "done";

// --- learning-path ("path mode") adapters -----------------------------------
// A concept step is content-shaped like one track (one narration, one
// audio_url per variant) with 2 questions (1 LOT + 1 HOT) attached, rather
// than a lesson's whole playlist. These adapt it to the two shapes this
// screen already knows how to play, so nothing below needs a second render
// path — only where a step transitions to the next one differs.
// See backend/adaptive/PATH_MODE.md for the ruling this mirrors.

function stepTrack(step: ApiStep, variant: Variant): ApiTrack {
  const version = step.versions[variant] ?? step.versions.normal;
  return {
    id: `step-${step.position}`,
    order: 0,
    title: step.title,
    type: "lesson_content",
    audio_url: version?.audio_url ?? "",
    audio_ready: Boolean(version?.audio_url),
    text: version?.text ?? "",
  };
}

// GeneratedQuestion never carries a correct_answer to the student (see
// learning_path/HANDOFF.md § 3), so the mapped correct_answer is always "" —
// QuestionCard's per-option "this was correct" highlight simply never
// matches, which is the desired behavior here, not a bug.
function stepQuestions(step: ApiStep): LegacyQuestion[] {
  return step.questions.map((q, index) => ({
    id: q.id,
    order: index,
    prompt: q.text,
    question_type: q.format === "TF" ? "true_false" : "multiple_choice",
    choices:
      q.format === "MCQ" && q.choices
        ? Object.keys(q.choices).sort().map((key) => q.choices![key])
        : [],
    correct_answer: "",
  }));
}

const VARIANT_INFO: Record<Variant, { icon: keyof typeof Ionicons.glyphMap; label: string } | null> = {
  normal: null,
  simplified: { icon: "reader-outline", label: "Simplified explanation" },
  elaborated: { icon: "sparkles-outline", label: "Extra detail" },
};

// A quiet, wide colored strip — used both for the "reviewing a prerequisite"
// notice and the variant badge. Always paired with an accessible label so a
// screen reader announces the same thing a sighted student sees.
function Notice({
  icon,
  tone,
  children,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  tone: "review" | "variant";
  children: string;
}) {
  const palette = tone === "review" ? styles.noticeReview : styles.noticeVariant;
  return (
    <View style={[styles.notice, palette]} accessible accessibilityRole="text" accessibilityLabel={children}>
      <Ionicons name={icon} size={16} color={tone === "review" ? colors.brand600 : colors.brand700} />
      <Text style={[styles.noticeText, tone === "variant" && { color: colors.brand700 }]}>{children}</Text>
    </View>
  );
}

const THINKING_LABEL: Record<string, string> = {
  LOT: "Quick check",
  HOT: "Think it through",
};

export default function LessonPlayerScreen() {
  const router = useRouter();
  const { courseId, lessonId } = useLocalSearchParams<{ courseId: string; lessonId: string }>();

  const [lesson, setLesson] = useState<ApiLesson | null>(null);
  const [learningStateId, setLearningStateId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [phase, setPhase] = useState<Phase>("audio");
  const [trackIndex, setTrackIndex] = useState(0);
  const [questionIndex, setQuestionIndex] = useState(0);

  // Path mode: which concept the student is on, its variant, and whether
  // they're mid-detour through an earlier prerequisite (non-null while so).
  const [pathStep, setPathStep] = useState<ApiStep | null>(null);
  const [variant, setVariant] = useState<Variant>("normal");
  const [remediationTarget, setRemediationTarget] = useState<number | null>(null);
  // The last submit-response result, read by nextQuestion() once the student
  // taps past the feedback QuestionCard shows — the state transition (which
  // concept/variant comes next) was already decided server-side by then.
  const lastResult = useRef<ApiSubmitResult | null>(null);

  const inPathMode = pathStep !== null;
  const tracks = inPathMode ? [stepTrack(pathStep, variant)] : lesson?.tracks ?? [];
  const questions = inPathMode ? stepQuestions(pathStep) : lesson?.questions ?? [];
  const track = tracks[trackIndex];
  const hasQuestions = inPathMode ? questions.length > 0 : lesson?.has_questions ?? false;
  const canGrade = Boolean(learningStateId) && hasQuestions;

  const goToQuestions = useCallback(() => {
    setPhase(canGrade ? "questions" : "done");
  }, [canGrade]);

  const handleTrackFinish = useCallback(() => {
    setTrackIndex((current) => {
      if (current + 1 < tracks.length) return current + 1;
      goToQuestions();
      return current;
    });
  }, [tracks.length, goToQuestions]);

  const player = useAudioPlayer(handleTrackFinish);
  const { load } = player;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      fetchLessonPackage(lessonId),
      startLearning(courseId).catch(() => null),
    ])
      .then(([pkg, start]) => {
        if (cancelled) return;
        setLesson(pkg);
        setLearningStateId(start?.learning_state?.id ?? null);

        const step = start?.current_step ?? null;
        setPathStep(step);
        if (step) {
          const startVariant = start!.learning_state.current_variant || "normal";
          const assignedId = start!.learning_state.current_generated_question;
          const idx = step.questions.findIndex((q) => q.id === assignedId);
          setQuestionIndex(idx >= 0 ? idx : 0);
          setVariant(startVariant);
          setRemediationTarget(start!.learning_state.remediation_target_position ?? null);
          setPhase(stepTrack(step, startVariant).audio_ready ? "audio" : "questions");
        } else if (pkg.tracks.length === 0) {
          setPhase(pkg.has_questions ? "questions" : "done");
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load this lesson.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [courseId, lessonId]);

  // Point the player at the current track whenever it changes.
  useEffect(() => {
    if (phase !== "audio" || !track) return;
    if (track.audio_ready) {
      load(resolveMediaUrl(track.audio_url), true);
    } else {
      load("", false);
    }
  }, [phase, trackIndex, track?.audio_ready, track?.audio_url, load]);

  const progress = useMemo(() => {
    if (!player.durationMillis) return 0;
    return Math.min(1, player.positionMillis / player.durationMillis);
  }, [player.positionMillis, player.durationMillis]);

  async function onSubmitAnswer(answer: string): Promise<SubmitResult> {
    const res = await submitResponse({
      learning_state_id: learningStateId as number,
      question_id: questions[questionIndex].id,
      selected_answer: answer,
    });
    if (inPathMode) lastResult.current = res;
    return { is_correct: res.is_correct, mastery: res.mastery, completed: res.completed };
  }

  // The ruling (backend/adaptive_portal/services.py::AdaptiveEngine.evaluate_path,
  // mirrored here as pure state transition, no re-computation):
  //   correct, more questions left in this concept -> next question, same concept
  //   correct, concept cleared               -> next concept in path order (or
  //                                              resume the one being detoured
  //                                              through, if any), variant "normal"
  //   wrong x3, concept has a prerequisite    -> jump to the nearest prerequisite
  //                                              concept as a refresher
  //   wrong x3, no prerequisite to fall back  -> same concept, de-escalated
  //   on                                        variant (normal -> simplified ->
  //                                              elaborated)
  function nextQuestionPathMode() {
    const res = lastResult.current;
    lastResult.current = null;
    if (!res || res.completed || !res.current_step) {
      setPathStep(null);
      setPhase("done");
      return;
    }

    const newStep = res.current_step;
    const sameConcept = pathStep?.concept_id === newStep.concept_id;
    const nextVariant = res.current_variant ?? "normal";

    setPathStep(newStep);
    setVariant(nextVariant);
    setRemediationTarget(res.remediation_target_position ?? null);

    if (sameConcept) {
      // Still the same chunk: either the LOT->HOT move within it, or a
      // de-escalated variant re-teaching the same pair. Stay on questions.
      const idx = newStep.questions.findIndex((q) => q.id === res.next_question);
      setQuestionIndex(idx >= 0 ? idx : 0);
      setPhase("questions");
      return;
    }

    // A different concept: advanced forward, detoured to a prerequisite, or
    // resumed the one that was struggled on. res.next_question is whichever
    // of its questions the server assigned first (not necessarily index 0 —
    // a resumed concept may already have its LOT question answered, in which
    // case the server assigns the HOT one directly).
    const idx = newStep.questions.findIndex((q) => q.id === res.next_question);
    setQuestionIndex(idx >= 0 ? idx : 0);
    setTrackIndex(0);
    setPhase(stepTrack(newStep, nextVariant).audio_ready ? "audio" : "questions");
  }

  function nextQuestion() {
    if (inPathMode) {
      nextQuestionPathMode();
      return;
    }
    setQuestionIndex((current) => {
      if (current + 1 < questions.length) return current + 1;
      setPhase("done");
      return current;
    });
  }

  if (loading) {
    return (
      <SafeAreaView style={styles.screen} edges={["top", "bottom"]}>
        <View style={styles.center}>
          <ActivityIndicator color={colors.brand600} size="large" />
        </View>
      </SafeAreaView>
    );
  }

  if (error) {
    return (
      <SafeAreaView style={styles.screen} edges={["top", "bottom"]}>
        <View style={styles.topRow}>
          <IconButton icon="chevron-back" accessibilityLabel="Back to lessons" onPress={() => router.back()} />
        </View>
        <View style={styles.center}>
          <Text style={styles.error}>{error}</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.screen} edges={["top", "bottom"]}>
      <View style={styles.topRow}>
        <IconButton icon="chevron-back" accessibilityLabel="Back to lessons" onPress={() => router.back()} />
        <Text style={styles.headerTitle} numberOfLines={1}>
          {lesson?.title}
        </Text>
        <View style={{ width: 40 }} />
      </View>

      <ScrollView contentContainerStyle={styles.body} showsVerticalScrollIndicator={false}>
        {inPathMode && phase !== "done" && remediationTarget !== null && (
          <Notice icon="return-up-back-outline" tone="review">
            Quick review before you continue — you'll pick back up where you left off.
          </Notice>
        )}
        {inPathMode && phase !== "done" && VARIANT_INFO[variant] && (
          <Notice icon={VARIANT_INFO[variant]!.icon} tone="variant">
            {VARIANT_INFO[variant]!.label}
          </Notice>
        )}

        {phase === "audio" && track && (
          <>
            <View style={styles.artWrap}>
              <GradientTile size={220} radius={radii.lg} icon="headset" />
            </View>

            <Text style={styles.trackTitle} numberOfLines={2} accessibilityRole="header">
              {track.title}
            </Text>
            {!inPathMode && (
              <Text style={styles.trackMeta}>
                Track {trackIndex + 1} of {tracks.length}
              </Text>
            )}

            {!track.audio_ready && (
              <Text style={styles.warn}>Audio for this track hasn’t been generated yet.</Text>
            )}
            {player.error && <Text style={styles.warn}>{player.error}</Text>}

            <View
              style={styles.progressTrack}
              accessible
              accessibilityRole="progressbar"
              accessibilityValue={{ min: 0, max: 100, now: Math.round(progress * 100) }}
            >
              <View style={[styles.progressFill, { width: `${progress * 100}%` }]} />
            </View>
            <View style={styles.times}>
              <Text style={styles.timeText}>{fmt(player.positionMillis)}</Text>
              <Text style={styles.timeText}>{fmt(player.durationMillis)}</Text>
            </View>

            <View style={styles.transport}>
              <IconButton
                icon="play-skip-back"
                accessibilityLabel="Previous track"
                size={44}
                disabled={trackIndex === 0}
                onPress={() => setTrackIndex((i) => Math.max(0, i - 1))}
              />
              <IconButton
                icon="play-back"
                accessibilityLabel="Rewind 15 seconds"
                size={40}
                disabled={!player.isLoaded}
                onPress={() => player.seek(player.positionMillis - 15000)}
              />
              <IconButton
                icon={player.isPlaying ? "pause" : "play"}
                accessibilityLabel={player.isPlaying ? "Pause" : "Play"}
                variant="filled"
                size={68}
                disabled={!track.audio_ready || !player.isLoaded}
                onPress={() => (player.isPlaying ? player.pause() : player.play())}
              />
              <IconButton
                icon="play-forward"
                accessibilityLabel="Forward 15 seconds"
                size={40}
                disabled={!player.isLoaded}
                onPress={() => player.seek(player.positionMillis + 15000)}
              />
              <IconButton
                icon="play-skip-forward"
                accessibilityLabel="Next track"
                size={44}
                disabled={trackIndex + 1 >= tracks.length}
                onPress={() => setTrackIndex((i) => Math.min(tracks.length - 1, i + 1))}
              />
            </View>

            {track.text ? <Text style={styles.narration}>{track.text}</Text> : null}

            <Button
              label={canGrade ? "Skip to questions" : "Finish lesson"}
              variant="ghost"
              onPress={goToQuestions}
              style={{ marginTop: spacing.lg }}
            />

            {!inPathMode && (
              <View style={styles.playlist}>
                {tracks.map((t, i) => (
                  <Pressable
                    key={t.id}
                    onPress={() => setTrackIndex(i)}
                    style={[styles.playlistItem, i === trackIndex && styles.playlistItemActive]}
                  >
                    <Text style={styles.playlistIndex}>{i + 1}</Text>
                    <Text style={styles.playlistTitle} numberOfLines={1}>
                      {t.title}
                    </Text>
                    {!t.audio_ready && <Text style={styles.playlistFlag}>no audio</Text>}
                  </Pressable>
                ))}
              </View>
            )}
          </>
        )}

        {phase === "questions" && inPathMode && pathStep && (
          <View style={styles.conceptHeader}>
            <Text style={styles.conceptTitle} numberOfLines={2} accessibilityRole="header">
              {pathStep.title}
            </Text>
            {pathStep.questions[questionIndex] && (
              <View style={styles.thinkingTag}>
                <Text style={styles.thinkingTagText}>
                  {THINKING_LABEL[pathStep.questions[questionIndex].thinking_order] ?? "Question"}
                </Text>
              </View>
            )}
          </View>
        )}

        {phase === "questions" && questions[questionIndex] && (
          <QuestionCard
            key={questions[questionIndex].id}
            question={questions[questionIndex]}
            index={questionIndex}
            total={questions.length}
            onSubmit={onSubmitAnswer}
            onNext={nextQuestion}
          />
        )}

        {phase === "done" && (
          <View style={styles.doneCard}>
            <GradientTile size={120} radius={radii.lg} icon="checkmark" />
            <Text style={styles.doneTitle}>
              {lesson?.has_questions ? "Lesson complete" : "Lesson reviewed"}
            </Text>
            <Text style={styles.doneBody}>
              {lesson?.has_questions
                ? "Your answers have been saved."
                : "This lesson has no questions — you’ve listened through it in review-only mode."}
            </Text>
            <Button label="Back to lessons" onPress={() => router.back()} />
          </View>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.page, paddingHorizontal: spacing.lg },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  topRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginTop: spacing.sm,
    gap: spacing.sm,
  },
  headerTitle: { flex: 1, textAlign: "center", fontSize: 15, fontWeight: "800", color: colors.ink },
  body: { paddingBottom: spacing.xl, gap: spacing.xs },
  notice: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.xs,
    marginTop: spacing.sm,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
    borderRadius: radii.pill,
    alignSelf: "center",
  },
  noticeReview: { backgroundColor: colors.brand100 },
  noticeVariant: { backgroundColor: colors.brand50, borderWidth: 1, borderColor: colors.brand200 },
  noticeText: { fontSize: 13, fontWeight: "700", color: colors.brand600 },
  conceptHeader: {
    marginTop: spacing.lg,
    alignItems: "center",
    gap: spacing.xs,
  },
  conceptTitle: { fontSize: 18, fontWeight: "800", color: colors.ink, textAlign: "center" },
  thinkingTag: {
    paddingVertical: 4,
    paddingHorizontal: spacing.sm,
    borderRadius: radii.pill,
    backgroundColor: colors.panel,
    borderWidth: 1,
    borderColor: colors.border,
  },
  thinkingTagText: {
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 0.5,
    textTransform: "uppercase",
    color: colors.muted,
  },
  artWrap: { alignItems: "center", marginTop: spacing.lg },
  trackTitle: { marginTop: spacing.lg, fontSize: 20, fontWeight: "800", color: colors.ink, textAlign: "center" },
  trackMeta: { marginTop: 4, fontSize: 12, color: colors.faint, textAlign: "center" },
  warn: { marginTop: spacing.sm, fontSize: 12, color: colors.warning, textAlign: "center" },
  progressTrack: {
    marginTop: spacing.lg,
    height: 6,
    borderRadius: radii.pill,
    backgroundColor: colors.brand100,
    overflow: "hidden",
  },
  progressFill: { height: "100%", backgroundColor: colors.brand600 },
  times: { flexDirection: "row", justifyContent: "space-between", marginTop: 6 },
  timeText: { fontSize: 11, color: colors.faint, fontVariant: ["tabular-nums"] },
  transport: {
    marginTop: spacing.lg,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
  },
  narration: {
    marginTop: spacing.lg,
    fontSize: 14,
    lineHeight: 21,
    color: colors.muted,
  },
  playlist: { marginTop: spacing.xl, gap: spacing.xs },
  playlistItem: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
    borderRadius: radii.sm,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  playlistItemActive: { borderColor: colors.brand500, backgroundColor: colors.brand50 },
  playlistIndex: { fontSize: 12, fontWeight: "800", color: colors.faint, width: 18 },
  playlistTitle: { flex: 1, fontSize: 13, fontWeight: "600", color: colors.ink },
  playlistFlag: { fontSize: 10, fontWeight: "800", color: colors.warning, textTransform: "uppercase" },
  doneCard: { alignItems: "center", gap: spacing.md, marginTop: spacing.xl },
  doneTitle: { fontSize: 20, fontWeight: "800", color: colors.ink },
  doneBody: { fontSize: 14, color: colors.muted, textAlign: "center" },
  error: { fontSize: 14, fontWeight: "600", color: colors.danger, textAlign: "center" },
});
