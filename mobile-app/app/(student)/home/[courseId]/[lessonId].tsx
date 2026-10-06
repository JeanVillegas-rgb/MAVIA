import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";

import IconButton from "@/components/IconButton";
import GradientTile from "@/components/GradientTile";
import Button from "@/components/Button";
import QuestionCard, { SubmitResult } from "@/components/QuestionCard";
import { useAudioPlayer } from "@/hooks/useAudioPlayer";
import { useNarration } from "@/hooks/useNarration";
import { useBrailleKeypad } from "@/input/useBrailleKeypad";
import { useGuideBusy } from "@/guide/GuideActivity";
import { silenceAll } from "@/hooks/audioBus";
import { setFinishedTopic } from "@/nav/finishedTopic";
import { describeCommand, logResponse, resetResponseLog } from "@/player/responseLog";
import {
  ApiAnswerResult,
  ApiError,
  Variant,
  continueTopic,
  fetchTopicPackage,
  resolveMediaUrl,
  submitAnswer,
} from "@/api/client";
import {
  INITIAL_STATE,
  PlayerState,
  afterAudio,
  applyCommand,
  applyPackage,
  cardKey,
  currentStep,
  questionsFor,
  stepLabel,
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
  standard: null,
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

// What the repeat key asks, and how long it waits for an answer.
const REPEAT_PROMPT =
  "Do you want to hear the topic again, or the question? " +
  "Press A for the topic, or B for the question.";
const REPEAT_MENU_TIMEOUT_MS = 12000;

export default function LessonPlayerScreen() {
  const router = useRouter();
  // `lessonId` is the topic being played (a published topic of the course outline).
  const { courseId, lessonId } = useLocalSearchParams<{ courseId: string; lessonId: string }>();

  // The traversal lives in @/player/traversal as pure functions, so it can be
  // driven against the live API from a script. This screen only holds the
  // result and renders it.
  const [state, setState] = useState<PlayerState>(INITIAL_STATE);
  const { topic, variant, returnTo, phase, trackIndex, questionIndex, finishedBefore } = state;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // One line explaining why the content is about to change, spoken just
  // before the narration it introduces. Mirrored into a ref so consuming it
  // never re-runs the playback effect on its own.
  const pendingAnnouncement = useRef<string | null>(null);
  // The last graded answer, read by nextQuestion() once the student taps past
  // the feedback QuestionCard shows -- the engine already decided what comes
  // next by then; the screen only follows its command.
  const lastResult = useRef<ApiAnswerResult | null>(null);
  // Bumped by the voice commands. `topicReplay` restarts the concept's
  // narration from its first part; `questionRepeat` re-reads the open question.
  const [topicReplay, setTopicReplay] = useState(0);
  const [questionRepeat, setQuestionRepeat] = useState(0);
  // True while an answer is on its way to the server. Leaving the question for
  // the concept then would unmount the card mid-submit and lose its verdict.
  const submittingRef = useRef(false);

  const step = currentStep(state);
  const tracks = tracksFor(state);
  const questions = questionsFor(state);
  const track = tracks[trackIndex];
  // A question to ask after this listening (false for a listen-only step, and
  // after a missed True/False with nothing fair left to ask).
  const hasQuestions = questions.length > 0 && state.awaitingQuestion;

  const setTrackIndex = useCallback((next: (current: number) => number) => {
    setState((prev) => ({ ...prev, trackIndex: next(prev.trackIndex) }));
  }, []);

  // Done listening (or skipping ahead): the step's questions, or -- for a
  // listen-only step -- ask the server to move on.
  const finishListening = useCallback(() => {
    setState((prev) => afterAudio(prev));
  }, []);

  // Advance within the step's clips, then fall through to the questions once
  // the last one has played. Derived from `prev` so it stays correct no
  // matter when the audio finishes.
  const handleTrackFinish = useCallback(() => {
    setState((prev) => {
      const total = tracksFor(prev).length;
      if (prev.trackIndex + 1 < total) return { ...prev, trackIndex: prev.trackIndex + 1 };
      return afterAudio(prev);
    });
  }, []);

  const player = useAudioPlayer(handleTrackFinish);
  const { load, stop: stopAudio } = player;
  const { speak, stop: stopNarration } = useNarration();

  // Read through a ref inside the playback effect, so a new callback identity
  // never reloads (and restarts) audio mid-playback.
  const finishTrackRef = useRef(handleTrackFinish);
  finishTrackRef.current = handleTrackFinish;

  // Open the topic's audio package where this student left off.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    resetResponseLog();
    fetchTopicPackage(lessonId)
      .then((pkg) => {
        if (!cancelled) setState(applyPackage(pkg));
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load this topic.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [lessonId]);

  // Point the player at the current clip whenever it changes. Order matters
  // for someone working by ear: the spoken hand-off ("here's the same idea,
  // explained more simply") plays first, then the narration it introduces --
  // never the two at once. A step with no audio hands on immediately, so a
  // missing clip stalls nobody.
  // A lesson never plays underneath the guide: the guide wins, and the lesson
  // picks up the moment it stops, because this effect re-runs when guideBusy
  // clears.
  const guideBusy = useGuideBusy();
  useEffect(() => {
    if (guideBusy) {
      stopNarration();
      stopAudio();
      return;
    }
    // Nothing plays until the topic has loaded. Before then there is no step
    // and no clip, and "no clip" would otherwise count as "finished" -- which
    // asked the server to move past the intro before it was ever heard.
    if (phase !== "audio" || loading || !step) return;
    const line = pendingAnnouncement.current;
    pendingAnnouncement.current = null;

    let cancelled = false;
    const startTrack = () => {
      if (cancelled) return;
      if (track?.audio_ready) {
        load(resolveMediaUrl(track.audio_url), true);
        return;
      }
      load("", false);
      finishTrackRef.current();
    };

    if (line) speak(line, { onDone: startTrack });
    else startTrack();

    return () => {
      // Only one voice at a time: never leave a half-played clip or a
      // half-read hand-off talking underneath whatever speaks next.
      cancelled = true;
      stopNarration();
      stopAudio();
    };
  }, [guideBusy, phase, loading, step?.position, trackIndex, track?.audio_ready, track?.audio_url, load, speak, stopNarration, stopAudio, topicReplay]);

  // A clip that fails to load hands on like a missing one: with no skip
  // button, nothing else would move the student past it.
  useEffect(() => {
    if (phase === "audio" && player.error) finishTrackRef.current();
  }, [phase, player.error]);

  // Belt and braces for the phases that have no audio effect of their own.
  useEffect(() => {
    if (phase === "audio") return;
    stopAudio();
  }, [phase, stopAudio]);

  // A listen-only step has been heard: the server decides where to go next.
  const continuingRef = useRef(false);
  useEffect(() => {
    if (phase !== "continue" || continuingRef.current) return;
    continuingRef.current = true;
    const from = state.position;
    continueTopic(lessonId)
      .then(({ next }) => {
        if (__DEV__) console.log(`[MAVIA] continue (step ${from}) :: ${describeCommand(from, next)}`);
        setState((prev) => {
          const moved = applyCommand(prev, next);
          pendingAnnouncement.current = moved.announcement;
          return moved;
        });
      })
      .catch(async (err) => {
        // 409: the server says this student is not on a listen-only step --
        // the app and the server disagree about where they are (e.g. a
        // continue that went through but whose reply never arrived). The
        // server is the record, so pick up from wherever it has them.
        if (err instanceof ApiError && err.status === 409) {
          try {
            const pkg = await fetchTopicPackage(lessonId);
            if (__DEV__) console.log(`[MAVIA] continue refused; resyncing to step ${pkg.progress.current_step_position}`);
            setState(applyPackage(pkg));
            return;
          } catch {
            // fall through to the error below
          }
        }
        setError(err instanceof Error ? err.message : "Couldn't continue this topic.");
      })
      .finally(() => {
        continuingRef.current = false;
      });
  }, [phase, lessonId, state.position]);

  // Finishing is the one moment the player has nothing left to play, and a
  // card saying so is no use to someone who cannot see it. Hand the topic's
  // name to the topics screen and go back to it: that screen says what was
  // finished and reads what to do next.
  // A topic that was already finished before it was opened keeps the card
  // instead -- nothing was just finished.
  const leavingRef = useRef(false);
  useEffect(() => {
    if (phase !== "done" || finishedBefore || leavingRef.current || !topic) return;
    leavingRef.current = true;
    const leave = () => {
      setFinishedTopic(topic.title);
      silenceAll();
      if (router.canGoBack()) router.back();
      else router.replace(`/home/${courseId}`);
    };
    // The last segment's review, if it had misses, is heard before leaving.
    const review = pendingAnnouncement.current;
    pendingAnnouncement.current = null;
    if (review) speak(review, { onDone: leave });
    else leave();
  }, [phase, finishedBefore, topic, router, courseId, speak]);

  // --- say it again -----------------------------------------------------------
  // Play the concept again from its first part. Asked from a question, the
  // learner goes back to the concept and returns to the same question after it
  // -- but not while an answer is being graded or its verdict is playing, where
  // leaving would drop the result.
  const replayTopic = useCallback(() => {
    if (phase === "done" || phase === "continue") return;
    if (phase === "questions" && (submittingRef.current || lastResult.current)) return;
    setState((prev) => ({ ...prev, phase: "audio", trackIndex: 0 }));
    setTopicReplay((n) => n + 1);
  }, [phase]);

  // Read the open question again. Asked while the concept is still playing,
  // go on to the question now -- it is read aloud as it opens.
  const replayQuestion = useCallback(() => {
    if (phase === "questions") setQuestionRepeat((n) => n + 1);
    else if (phase === "audio" && hasQuestions) finishListening();
  }, [phase, hasQuestions, finishListening]);

  // The repeat key asks which: "the topic, or the question?" -- answered with
  // A / B on the keypad. "asking" while the
  // prompt is spoken, "waiting" once it has finished and the answer is
  // listened for. While open, A and B answer the menu, never the question.
  type RepeatMenu = "closed" | "asking" | "waiting";
  const [repeatMenu, setRepeatMenu] = useState<RepeatMenu>("closed");
  const repeatMenuRef = useRef<RepeatMenu>("closed");
  repeatMenuRef.current = repeatMenu;
  const repeatTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The lesson audio was playing when the menu opened, so cancelling resumes it.
  const resumeAfterMenu = useRef(false);

  const closeRepeatMenu = useCallback(() => {
    if (repeatTimer.current) clearTimeout(repeatTimer.current);
    repeatTimer.current = null;
    repeatMenuRef.current = "closed";
    setRepeatMenu("closed");
  }, []);

  const answerRepeatMenu = useCallback(
    (which: "topic" | "question") => {
      closeRepeatMenu();
      resumeAfterMenu.current = false;
      stopNarration();
      if (which === "topic") replayTopic();
      else replayQuestion();
    },
    [closeRepeatMenu, stopNarration, replayTopic, replayQuestion]
  );

  // No answer (the pause key, or nobody replied): carry on as if never asked.
  const cancelRepeatMenu = useCallback(() => {
    closeRepeatMenu();
    stopNarration();
    if (resumeAfterMenu.current && player.isLoaded) player.play();
    resumeAfterMenu.current = false;
  }, [closeRepeatMenu, stopNarration, player]);
  const cancelRepeatMenuRef = useRef(cancelRepeatMenu);
  cancelRepeatMenuRef.current = cancelRepeatMenu;

  const openRepeatMenu = useCallback(() => {
    if (phase !== "audio" && phase !== "questions") return;
    if (phase === "questions" && (submittingRef.current || lastResult.current)) return;
    // Only the topic to repeat (a listen-only step): nothing to choose between.
    if (!hasQuestions) {
      replayTopic();
      return;
    }
    if (repeatMenuRef.current === "closed") resumeAfterMenu.current = phase === "audio" && player.isPlaying;
    if (player.isPlaying) player.pause();
    // The question's own clip belongs to QuestionCard; silenceAll reaches it.
    if (phase === "questions") silenceAll();
    if (repeatTimer.current) clearTimeout(repeatTimer.current);
    repeatMenuRef.current = "asking";
    setRepeatMenu("asking");
    speak(REPEAT_PROMPT, {
      onDone: () => {
        if (repeatMenuRef.current !== "asking") return;
        repeatMenuRef.current = "waiting";
        setRepeatMenu("waiting");
        repeatTimer.current = setTimeout(() => cancelRepeatMenuRef.current(), REPEAT_MENU_TIMEOUT_MS);
      },
    });
  }, [phase, hasQuestions, replayTopic, player, speak]);

  useEffect(
    () => () => {
      if (repeatTimer.current) clearTimeout(repeatTimer.current);
    },
    []
  );

  // --- pause / play --------------------------------------------------------------
  // Listening: pause the clip, or play it on from where it stopped. In the
  // question segment: stop the question being read, or read it again --
  // never while an answer is being graded, where stopping the verdict would
  // leave the card waiting forever.
  const questionReadingStopped = useRef(false);
  const currentCard = cardKey(state);
  useEffect(() => {
    questionReadingStopped.current = false;
  }, [currentCard]);

  const togglePause = useCallback(() => {
    if (phase === "audio") {
      if (player.isPlaying) player.pause();
      else if (player.isLoaded) player.play();
      // Nothing loaded (stopped during the spoken hand-off): start this part again.
      else setTopicReplay((n) => n + 1);
      return;
    }
    if (phase === "questions") {
      if (submittingRef.current || lastResult.current) return;
      if (!questionReadingStopped.current) {
        questionReadingStopped.current = true;
        silenceAll();
      } else {
        questionReadingStopped.current = false;
        setQuestionRepeat((n) => n + 1);
      }
    }
  }, [phase, player]);

  // Voice commands are switched off for now (not reliable enough); the keypad
  // and taps are the ways in. src/voice/ is kept for when they come back.
  const menuOpen = repeatMenu !== "closed";

  // Keypad: * asks "topic or question?", / pauses and plays. The answer keys
  // answer the menu while it is open (QuestionCard is told to ignore them
  // then), and the question otherwise. Back (-) is the layout's.
  useBrailleKeypad(
    (action) => {
      if (repeatMenuRef.current !== "closed") {
        if (action.kind === "answer") {
          if (action.letter === "a") answerRepeatMenu("topic");
          else if (action.letter === "b") answerRepeatMenu("question");
          else speak("Press A for the topic, or B for the question.");
        } else if (action.kind === "repeat") openRepeatMenu();
        else if (action.kind === "pause") cancelRepeatMenu();
        return;
      }
      if (action.kind === "repeat") openRepeatMenu();
      else if (action.kind === "pause") togglePause();
    },
    // While the guide is open, its keys are the guide's (see the layout).
    { enabled: !loading && !error && !guideBusy }
  );

  const progress = useMemo(() => {
    if (!player.durationMillis) return 0;
    return Math.min(1, player.positionMillis / player.durationMillis);
  }, [player.positionMillis, player.durationMillis]);

  async function onSubmitAnswer(answer: string): Promise<SubmitResult> {
    submittingRef.current = true;
    const sent = {
      questionId: questions[questionIndex].id,
      selectedAnswer: answer,
      stepPosition: state.position,
      variant,
    };
    let res: ApiAnswerResult;
    try {
      res = await submitAnswer({ topicId: lessonId, questionId: sent.questionId, selectedAnswer: answer });
    } finally {
      submittingRef.current = false;
    }
    // The run-through's transcript, in the Metro console. See player/responseLog.
    logResponse(sent, res);
    lastResult.current = res;
    // Mastery stays on the server during calibration (shadow mode), so the
    // card is given none to show.
    return { is_correct: res.is_correct, mastery: 0, completed: res.next.action === "complete" };
  }

  // One graded answer, one command. The engine already decided on the server;
  // the screen follows it. See @/player/traversal.applyCommand.
  function nextQuestion() {
    const res = lastResult.current;
    lastResult.current = null;
    if (!res) return;
    setState((prev) => {
      const next = applyCommand(prev, res.next);
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
          <IconButton icon="chevron-back" accessibilityLabel="Back to topics" onPress={() => router.back()} />
        </View>
        <View style={styles.center}>
          <Text style={styles.error}>{error}</Text>
        </View>
      </SafeAreaView>
    );
  }

  const thinking = step?.questions[questionIndex]?.thinking_order;

  // The audio player. `waiting` greys it out for the question segment: the
  // concept stays on screen, but nothing plays until the answer is in.
  // It stays on screen for a step with no audio, too, and while the app asks
  // the server to move past a listen-only step; controls with nothing to act
  // on are disabled rather than hidden.
  function renderPlayer(waiting: boolean) {
    const live = phase === "audio" && !waiting;
    return (
      <View style={waiting && styles.waiting} pointerEvents={waiting ? "none" : "auto"}>
        <View style={[styles.segment, waiting ? styles.segmentQuestion : styles.segmentListen]}>
          <Ionicons
            name={waiting ? "help-circle-outline" : "headset-outline"}
            size={14}
            color={waiting ? colors.brand700 : colors.brand600}
          />
          <Text style={styles.segmentText}>
            {waiting ? `Question time${thinking ? ` · ${THINKING_LABEL[thinking] ?? "Question"}` : ""}` : "Listening"}
          </Text>
        </View>

        <Text style={styles.trackTitle} numberOfLines={2} accessibilityRole="header">
          {waiting ? stepLabel(state) : track?.title ?? stepLabel(state)}
        </Text>

        <View style={styles.artWrap}>
          <GradientTile size={248} radius={124} icon={waiting ? "help" : "headset"} />
        </View>
        {!waiting && tracks.length > 1 && (
          <Text style={styles.trackMeta}>
            Part {trackIndex + 1} of {tracks.length}
          </Text>
        )}

        {!waiting && player.error && <Text style={styles.warn}>{player.error}</Text>}

        <View
          style={styles.progressTrack}
          accessible={!waiting}
          accessibilityRole="progressbar"
          accessibilityValue={{ min: 0, max: 100, now: Math.round((waiting ? 1 : progress) * 100) }}
        >
          <View style={[styles.progressFill, { width: `${(waiting ? 1 : progress) * 100}%` }]} />
        </View>
        <View style={styles.times}>
          <Text style={styles.timeText}>{fmt(player.positionMillis)}</Text>
          <Text style={styles.timeText}>{fmt(player.durationMillis)}</Text>
        </View>

        <View style={styles.transport}>
          <IconButton
            icon="play-skip-back"
            accessibilityLabel="Previous part"
            size={44}
            disabled={!live || trackIndex === 0}
            onPress={() => setTrackIndex((i: number) => Math.max(0, i - 1))}
          />
          <IconButton
            icon="play-back"
            accessibilityLabel="Rewind 15 seconds"
            size={40}
            disabled={!live || !player.isLoaded}
            onPress={() => player.seek(player.positionMillis - 15000)}
          />
          <IconButton
            icon={player.isPlaying ? "pause" : "play"}
            accessibilityLabel={player.isPlaying ? "Pause" : "Play"}
            variant="filled"
            size={68}
            disabled={!live || !track?.audio_ready || !player.isLoaded}
            onPress={() => (player.isPlaying ? player.pause() : player.play())}
          />
          <IconButton
            icon="play-forward"
            accessibilityLabel="Forward 15 seconds"
            size={40}
            disabled={!live || !player.isLoaded}
            onPress={() => player.seek(player.positionMillis + 15000)}
          />
          <IconButton
            icon="play-skip-forward"
            accessibilityLabel="Next part"
            size={44}
            disabled={!live || trackIndex + 1 >= tracks.length}
            onPress={() => setTrackIndex((i: number) => Math.min(tracks.length - 1, i + 1))}
          />
        </View>

        {/* No skip button: the concept is always heard before its questions. */}
        {!waiting && phase === "continue" && (
          <ActivityIndicator color={colors.brand600} style={{ marginTop: spacing.lg }} />
        )}
      </View>
    );
  }

  return (
    <SafeAreaView style={styles.screen} edges={["top", "bottom"]}>
      <View style={styles.topRow}>
        <IconButton icon="chevron-back" accessibilityLabel="Back to topics" onPress={() => router.back()} />
        <Text style={styles.headerTitle} numberOfLines={1}>
          {topic?.title}
        </Text>
        <View style={{ width: 40 }} />
      </View>

      <ScrollView contentContainerStyle={styles.body} showsVerticalScrollIndicator={false}>
        {phase !== "done" && returnTo !== null && (
          <Notice icon="return-up-back-outline" tone="review">
            Quick review before you continue — you'll pick back up where you left off.
          </Notice>
        )}
        {menuOpen && (
          <Notice icon="repeat-outline" tone="review">
            Repeat what? A — the topic · B — the question
          </Notice>
        )}
        {phase !== "done" && VARIANT_INFO[variant] && (
          <Notice icon={VARIANT_INFO[variant]!.icon} tone="variant">
            {VARIANT_INFO[variant]!.label}
          </Notice>
        )}

        {(phase === "audio" || phase === "continue") && renderPlayer(false)}

        {/* The question segment keeps the player on screen, greyed out, with
            the question read aloud rather than shown. Answering is by voice,
            the keypad, or tap counting anywhere on the player. */}
        {phase === "questions" && questions[questionIndex] && (
          <QuestionCard
            key={cardKey(state)}
            question={questions[questionIndex]}
            index={questionIndex}
            total={questions.length}
            onSubmit={onSubmitAnswer}
            onNext={nextQuestion}
            repeatSignal={questionRepeat}
            face={renderPlayer(true)}
            inputPaused={menuOpen}
          />
        )}

        {phase === "done" && (
          <View style={styles.doneCard}>
            <GradientTile size={120} radius={radii.lg} icon="checkmark" />
            <Text style={styles.doneTitle}>{finishedBefore ? "Topic already finished" : "Topic complete"}</Text>
            <Text style={styles.doneBody}>
              {finishedBefore
                ? "You've already worked through this topic. Your answers are saved."
                : "Your answers have been saved."}
            </Text>
            <Button label="Back to topics" onPress={() => router.back()} />
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
  headerTitle: {
    flex: 1,
    textAlign: "center",
    fontSize: 13,
    fontWeight: "700",
    letterSpacing: 0.6,
    textTransform: "uppercase",
    color: colors.faint,
  },
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
  noticeVariant: { backgroundColor: colors.brand50 },
  noticeText: { fontSize: 14, fontWeight: "700", color: colors.brand600 },
  // Greyed out while the question segment waits for an answer.
  waiting: { opacity: 0.38 },
  // Which segment of the concept this is: listening, or question time.
  segment: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "center",
    gap: 6,
    marginTop: spacing.lg,
    paddingVertical: 5,
    paddingHorizontal: spacing.md,
    borderRadius: radii.pill,
  },
  segmentListen: { backgroundColor: colors.brand50 },
  segmentQuestion: { backgroundColor: colors.brand100 },
  segmentText: {
    fontSize: 12,
    fontWeight: "800",
    letterSpacing: 0.5,
    textTransform: "uppercase",
    color: colors.brand700,
  },
  artWrap: { alignItems: "center", marginTop: spacing.xl },
  trackTitle: {
    marginTop: spacing.xl,
    fontSize: 27,
    lineHeight: 33,
    fontWeight: "800",
    letterSpacing: -0.5,
    color: colors.ink,
    textAlign: "center",
  },
  trackMeta: { marginTop: 4, fontSize: 12, color: colors.faint, textAlign: "center" },
  warn: { marginTop: spacing.sm, fontSize: 12, color: colors.warning, textAlign: "center" },
  progressTrack: {
    marginTop: spacing.xl,
    height: 4,
    borderRadius: radii.pill,
    backgroundColor: colors.brand100,
    overflow: "hidden",
  },
  progressFill: { height: "100%", backgroundColor: colors.brand600 },
  times: { flexDirection: "row", justifyContent: "space-between", marginTop: 10 },
  timeText: { fontSize: 13, color: colors.faint, fontVariant: ["tabular-nums"] },
  transport: {
    marginTop: spacing.xl,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.lg,
  },
  doneCard: { alignItems: "center", gap: spacing.md, marginTop: spacing.xl },
  doneTitle: { fontSize: 20, fontWeight: "800", color: colors.ink },
  doneBody: { fontSize: 14, color: colors.muted, textAlign: "center" },
  error: { fontSize: 14, fontWeight: "600", color: colors.danger, textAlign: "center" },
});
