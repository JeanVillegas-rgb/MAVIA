import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";

import IconButton from "@/components/IconButton";
import GradientTile from "@/components/GradientTile";
import Button from "@/components/Button";
import QuestionCard, { SubmitResult } from "@/components/QuestionCard";
import { useAudioPlayer } from "@/hooks/useAudioPlayer";
import { useNarration } from "@/hooks/useNarration";
import { useVoiceCommands } from "@/voice/useVoiceCommands";
import {
  ApiSubmitResult,
  Variant,
  fetchLessonPackage,
  resolveMediaUrl,
  startLearning,
  submitResponse,
} from "@/api/client";
import {
  INITIAL_STATE,
  PlayerState,
  applyResult,
  applyStart,
  cardKey,
  questionsFor,
  stepChunk,
  tracksFor,
} from "@/player/traversal";
import { colors, radii, spacing } from "@/theme";

function fmt(millis: number) {
  if (!millis || millis < 0) return "0:00";
  const total = Math.floor(millis / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
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

  // The whole traversal lives in @/player/traversal as pure functions, so it
  // can be replayed against real API payloads in a test harness. This screen
  // only holds the result and renders it.
  const [state, setState] = useState<PlayerState>(INITIAL_STATE);
  const { lesson, pathStep, variant, currentChunk, remediationTarget, phase, trackIndex, questionIndex, askCount } =
    state;

  const [learningStateId, setLearningStateId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // One line explaining why the content is about to change, spoken just
  // before the narration it introduces. Mirrored into a ref so consuming it
  // never re-runs the playback effect on its own.
  const pendingAnnouncement = useRef<string | null>(null);
  // The last submit-response result, read by nextQuestion() once the student
  // taps past the feedback QuestionCard shows — the state transition (which
  // concept/variant comes next) was already decided server-side by then.
  const lastResult = useRef<ApiSubmitResult | null>(null);
  // Bumped by the voice commands. `topicReplay` restarts the concept's
  // narration from its first part; `questionRepeat` re-reads the open question.
  const [topicReplay, setTopicReplay] = useState(0);
  const [questionRepeat, setQuestionRepeat] = useState(0);
  // True while an answer is on its way to the server. Leaving the question for
  // the topic then would unmount the card mid-submit and lose its verdict.
  const submittingRef = useRef(false);

  const inPathMode = pathStep !== null;
  const tracks = tracksFor(state);
  const questions = questionsFor(state);
  const track = tracks[trackIndex];
  const hasQuestions = inPathMode ? questions.length > 0 : lesson?.has_questions ?? false;
  const canGrade = Boolean(learningStateId) && hasQuestions;

  const setTrackIndex = useCallback((next: (current: number) => number) => {
    setState((prev) => ({ ...prev, trackIndex: next(prev.trackIndex) }));
  }, []);

  const goToQuestions = useCallback(() => {
    setState((prev) => ({ ...prev, phase: canGrade ? "questions" : "done" }));
  }, [canGrade]);

  // Advance within the playlist, or fall through to the questions once the
  // last track has played. Derived from `prev` rather than the render's own
  // `tracks` so it stays correct no matter when the audio finishes.
  const handleTrackFinish = useCallback(() => {
    setState((prev) => {
      const total = tracksFor(prev).length;
      if (prev.trackIndex + 1 < total) return { ...prev, trackIndex: prev.trackIndex + 1 };
      return { ...prev, phase: canGrade ? "questions" : "done" };
    });
  }, [canGrade]);

  const player = useAudioPlayer(handleTrackFinish);
  const { load, stop: stopAudio } = player;
  const { speak, stop: stopNarration } = useNarration();

  // Read through a ref inside the playback effect: handleTrackFinish changes
  // identity whenever the track list or grading availability does, and
  // depending on it directly would reload (and restart) audio mid-playback.
  const finishTrackRef = useRef(handleTrackFinish);
  finishTrackRef.current = handleTrackFinish;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      fetchLessonPackage(lessonId),
      startLearning(courseId, lessonId).catch(() => null),
    ])
      .then(([pkg, start]) => {
        if (cancelled) return;
        setLearningStateId(start?.learning_state?.id ?? null);
        setState(applyStart(pkg, start));
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

  // Point the player at the current track whenever it changes, and keep the
  // student's ears busy the whole way through. Order matters for someone
  // working by ear alone: the spoken hand-off ("here's the same idea,
  // explained more simply") plays first, then the narration it introduces --
  // never the two at once. A version with no generated audio is read aloud by
  // the device and then hands on exactly as finished audio would, so a
  // missing mp3 stalls nobody.
  useEffect(() => {
    if (phase !== "audio" || !track) return;
    const line = pendingAnnouncement.current;
    pendingAnnouncement.current = null;

    let cancelled = false;
    const startTrack = () => {
      if (cancelled) return;
      if (track.audio_ready) {
        load(resolveMediaUrl(track.audio_url), true);
        return;
      }
      load("", false);
      if (track.text) {
        speak(track.text, {
          onDone: () => {
            if (!cancelled) finishTrackRef.current();
          },
        });
      } else {
        finishTrackRef.current();
      }
    };

    if (line) speak(line, { onDone: startTrack });
    else startTrack();

    return () => {
      // Only one voice at a time. This runs whenever the phase leaves "audio"
      // (skipping ahead, a re-teach, backing out) and whenever the track
      // changes, so neither a half-played mp3 nor a half-read narration is
      // ever left talking underneath whatever speaks next.
      cancelled = true;
      stopNarration();
      stopAudio();
    };
  }, [phase, trackIndex, track?.audio_ready, track?.audio_url, track?.text, load, speak, stopNarration, stopAudio, topicReplay]);

  // Belt and braces for the phases that have no audio effect of their own:
  // QuestionCard starts narrating from its own mount effect, which React runs
  // *before* the parent cleanup above, so a track could otherwise get a word
  // in over the top of the question.
  useEffect(() => {
    if (phase === "audio") return;
    stopAudio();
  }, [phase, stopAudio]);

  // The done card is the one screen with nothing else to hear -- say it.
  useEffect(() => {
    if (phase !== "done") return;
    speak(
      lesson?.has_questions
        ? "Lesson complete. Your answers have been saved."
        : "Lesson reviewed. You've listened all the way through."
    );
  }, [phase, lesson?.has_questions, speak]);

  // Voice commands for this screen. Add a handler here for each command in
  // src/voice/commands.ts that should do something while a lesson is open.
  useVoiceCommands(
    {
      // Play the topic again from its first part. Asked from a question, the
      // learner goes back to the topic and returns to the same question after
      // it -- but not while an answer is being graded or its verdict is
      // playing, where leaving would drop the result.
      repeatTopic: () => {
        if (phase === "done") return;
        if (phase === "questions" && (submittingRef.current || lastResult.current)) return;
        setState((prev) => ({ ...prev, phase: "audio", trackIndex: prev.pathStep ? 0 : prev.trackIndex }));
        setTopicReplay((n) => n + 1);
      },
      // Read the open question again. Asked while the topic is still playing,
      // go on to the question now -- it is read aloud as it opens.
      repeatQuestion: () => {
        if (phase === "questions") setQuestionRepeat((n) => n + 1);
        else if (phase === "audio" && canGrade) goToQuestions();
      },
    },
    { enabled: !loading && !error }
  );

  const progress = useMemo(() => {
    if (!player.durationMillis) return 0;
    return Math.min(1, player.positionMillis / player.durationMillis);
  }, [player.positionMillis, player.durationMillis]);

  async function onSubmitAnswer(answer: string): Promise<SubmitResult> {
    submittingRef.current = true;
    let res: ApiSubmitResult;
    try {
      res = await submitResponse({
        learning_state_id: learningStateId as number,
        question_id: questions[questionIndex].id,
        selected_answer: answer,
      });
    } finally {
      submittingRef.current = false;
    }
    lastResult.current = res;
    return { is_correct: res.is_correct, mastery: res.mastery, completed: res.completed };
  }

  // One graded answer, one state transition. The ruling itself
  // (backend/adaptive/services.py::AdaptiveEngine.evaluate_path) has already
  // run server-side by the time this fires; applyResult only decides how the
  // student is walked through whatever came back. See @/player/traversal.
  function nextQuestion() {
    const res = lastResult.current;
    lastResult.current = null;
    setState((prev) => {
      const next = applyResult(prev, res);
      // Hand the spoken lead-in to the playback effect, which plays it ahead
      // of the narration it introduces.
      pendingAnnouncement.current = next.announcement;
      return next;
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
        {inPathMode && phase !== "done" && currentChunk !== null && (
          <Notice icon="swap-horizontal-outline" tone="variant">
            A different explanation of this idea
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
                onPress={() => setTrackIndex((i: number) => Math.max(0, i - 1))}
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
                onPress={() => setTrackIndex((i: number) => Math.min(tracks.length - 1, i + 1))}
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
                    onPress={() => setTrackIndex(() => i)}
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
            {stepChunk(pathStep, currentChunk).questions[questionIndex] && (
              <View style={styles.thinkingTag}>
                <Text style={styles.thinkingTagText}>
                  {THINKING_LABEL[stepChunk(pathStep, currentChunk).questions[questionIndex].thinking_order] ??
                    "Question"}
                </Text>
              </View>
            )}
          </View>
        )}

        {phase === "questions" && questions[questionIndex] && (
          <QuestionCard
            key={cardKey(state)}
            question={questions[questionIndex]}
            index={questionIndex}
            total={questions.length}
            onSubmit={onSubmitAnswer}
            onNext={nextQuestion}
            repeatSignal={questionRepeat}
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
  body: { flexGrow: 1, paddingBottom: spacing.xl, gap: spacing.xs },
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
