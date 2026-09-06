import React, { useEffect, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";

import IconButton from "@/components/IconButton";
import GradientTile from "@/components/GradientTile";
import { Lesson, fetchLesson } from "@/data/library";
import { colors, radii, shadow, spacing } from "@/theme";

// No real audio playback yet — this screen is wired up (route params,
// data fetch, transport controls) but everything renders its honest "not
// loaded" state rather than fake position/duration numbers.
export default function LessonPlayerScreen() {
  const router = useRouter();
  const { courseId, lessonId } = useLocalSearchParams<{
    courseId: string;
    lessonId: string;
  }>();

  const [lesson, setLesson] = useState<Lesson | null>(null);
  const [favorited, setFavorited] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchLesson(courseId, lessonId).then((data) => {
      if (!cancelled) setLesson(data);
    });
    return () => {
      cancelled = true;
    };
  }, [courseId, lessonId]);

  const title = lesson?.title ?? "No lesson loaded";
  const hasAudio = Boolean(lesson);

  return (
    <SafeAreaView style={styles.screen} edges={["top", "bottom"]}>
      <View style={styles.topRow}>
        <IconButton
          icon="chevron-back"
          accessibilityLabel="Go back to lesson list"
          onPress={() => router.back()}
        />
        <IconButton
          icon={favorited ? "heart" : "heart-outline"}
          accessibilityLabel={favorited ? "Remove from favorites" : "Add to favorites"}
          selected={favorited}
          onPress={() => setFavorited((value) => !value)}
          disabled={!hasAudio}
        />
      </View>

      <View style={styles.artWrap}>
        <GradientTile
          size={260}
          radius={radii.lg}
          icon="headset"
          style={shadow.card}
          accessibilityLabel={`Cover art for ${title}`}
        />
      </View>

      <Text style={styles.title} numberOfLines={2} accessibilityRole="header">
        {title}
      </Text>
      {!hasAudio && (
        <Text style={styles.helper}>
          This lesson hasn&apos;t been published yet.
        </Text>
      )}

      <View
        style={styles.progressWrap}
        accessible
        accessibilityRole="adjustable"
        accessibilityLabel="Playback position"
        accessibilityValue={{
          min: 0,
          max: lesson?.durationSeconds ?? 0,
          now: 0,
          text: hasAudio ? `0 seconds of ${lesson?.durationLabel}` : "No lesson loaded",
        }}
      >
        <View style={styles.progressTrack}>
          <View style={styles.progressFill} />
          <View style={styles.progressThumb} />
        </View>
        <View style={styles.progressLabels}>
          <Text style={styles.progressLabel}>0:00</Text>
          <Text style={styles.progressLabel}>{lesson?.durationLabel ?? "--:--"}</Text>
        </View>
      </View>

      <View style={styles.transport}>
        <IconButton
          icon="play-skip-back"
          accessibilityLabel="Previous lesson"
          size={44}
          disabled={!hasAudio}
        />
        <IconButton
          icon="play"
          accessibilityLabel="Play"
          variant="filled"
          size={68}
          disabled={!hasAudio}
        />
        <IconButton
          icon="play-skip-forward"
          accessibilityLabel="Next lesson"
          size={44}
          disabled={!hasAudio}
        />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.page,
    paddingHorizontal: spacing.lg,
  },
  topRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: spacing.sm,
  },
  artWrap: { alignItems: "center", marginTop: spacing.xl },
  title: {
    marginTop: spacing.xl,
    fontSize: 22,
    fontWeight: "800",
    color: colors.ink,
    textAlign: "center",
  },
  helper: {
    marginTop: 6,
    fontSize: 13,
    color: colors.muted,
    textAlign: "center",
  },
  progressWrap: { marginTop: spacing.xl },
  progressTrack: {
    height: 6,
    borderRadius: radii.pill,
    backgroundColor: colors.brand100,
    justifyContent: "center",
  },
  progressFill: {
    position: "absolute",
    left: 0,
    top: 0,
    bottom: 0,
    width: "0%",
    borderRadius: radii.pill,
    backgroundColor: colors.brand600,
  },
  progressThumb: {
    position: "absolute",
    left: 0,
    width: 14,
    height: 14,
    borderRadius: 7,
    backgroundColor: colors.brand700,
    borderWidth: 2,
    borderColor: colors.white,
  },
  progressLabels: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 6,
  },
  progressLabel: { fontSize: 11, color: colors.faint, fontVariant: ["tabular-nums"] },
  transport: {
    marginTop: spacing.xl,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.xl,
  },
});
