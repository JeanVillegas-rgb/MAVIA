import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";

import IconButton from "@/components/IconButton";
import GradientTile from "@/components/GradientTile";
import Button from "@/components/Button";
import QuestionCard, { SubmitResult } from "@/components/QuestionCard";
import { useAudioPlayer } from "@/hooks/useAudioPlayer";
import {
  ApiLesson,
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

  const tracks = lesson?.tracks ?? [];
  const questions = lesson?.questions ?? [];
  const track = tracks[trackIndex];
  const canGrade = Boolean(learningStateId) && (lesson?.has_questions ?? false);

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
        if (pkg.tracks.length === 0) setPhase(pkg.has_questions ? "questions" : "done");
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
    return { is_correct: res.is_correct, mastery: res.mastery, completed: res.completed };
  }

  function nextQuestion() {
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
        {phase === "audio" && track && (
          <>
            <View style={styles.artWrap}>
              <GradientTile size={220} radius={radii.lg} icon="headset" />
            </View>

            <Text style={styles.trackTitle} numberOfLines={2} accessibilityRole="header">
              {track.title}
            </Text>
            <Text style={styles.trackMeta}>
              Track {trackIndex + 1} of {tracks.length}
            </Text>

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
          </>
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
