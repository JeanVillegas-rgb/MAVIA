import React, { useEffect, useMemo, useState } from "react";
import { Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import TopNav from "@/components/TopNav";
import PrimaryButton from "@/components/PrimaryButton";
import ProgressBar from "@/components/ProgressBar";
import VariantToggle from "@/components/VariantToggle";
import { fetchLessonPackage } from "@/data/api";
import { LessonPackage, Variant } from "@/data/types";
import { colors, fonts, radii, spacing } from "@/theme";

export default function LessonPlayer() {
  const { lessonId } = useLocalSearchParams<{ lessonId: string }>();
  const router = useRouter();

  const [lesson, setLesson] = useState<LessonPackage | null>(null);
  const [chunkIndex, setChunkIndex] = useState(0);
  const [variant, setVariant] = useState<Variant>("NORMAL");
  const [isPlaying, setIsPlaying] = useState(false);
  const [askOpen, setAskOpen] = useState(false);

  useEffect(() => {
    if (!lessonId) return;
    fetchLessonPackage(lessonId).then(setLesson);
  }, [lessonId]);

  const chunk = lesson?.chunks[chunkIndex];

  const availableVariants = useMemo(
    () => (chunk ? (Object.keys(chunk.variants) as Variant[]) : []),
    [chunk]
  );

  useEffect(() => {
    // reset to NORMAL (or whatever's first available) whenever chunk changes
    if (availableVariants.length && !availableVariants.includes(variant)) {
      setVariant(availableVariants[0]);
    }
  }, [availableVariants]);

  if (!lesson || !chunk) {
    return (
      <View style={styles.screen}>
        <TopNav showBack title="Loading…" />
      </View>
    );
  }

  const activeContent = chunk.variants[variant];
  const isLastChunk = chunkIndex === lesson.chunks.length - 1;

  const handleNext = () => {
    if (isLastChunk) {
      router.push(`/lesson/${lessonId}/questions`);
    } else {
      setChunkIndex((i) => i + 1);
      setIsPlaying(false);
    }
  };

  return (
    <View style={styles.screen}>
      <TopNav showBack title={lesson.title} />

      <View style={styles.progressWrap}>
        <ProgressBar progress={(chunkIndex + 1) / lesson.chunks.length} />
        <Text style={styles.progressLabel}>
          Chunk {chunkIndex + 1} of {lesson.chunks.length}
        </Text>
      </View>

      <View style={styles.card}>
        <View style={styles.cardHeader}>
          <Text style={styles.chunkTitle}>{chunk.title}</Text>
          {availableVariants.length > 1 && (
            <VariantToggle available={availableVariants} selected={variant} onSelect={setVariant} />
          )}
        </View>

        <Text style={styles.narration}>{activeContent?.narration}</Text>

        <Pressable style={styles.audioControl} onPress={() => setIsPlaying((p) => !p)}>
          <Ionicons name={isPlaying ? "pause-circle" : "play-circle"} size={40} color={colors.maroon900} />
          <Text style={styles.audioLabel}>
            {activeContent?.audioUrl ? (isPlaying ? "Playing narration…" : "Play narration") : "Audio not generated yet"}
          </Text>
        </Pressable>
      </View>

      <View style={styles.footer}>
        <PrimaryButton
          label={isLastChunk ? "Start Questions" : "Next"}
          icon={isLastChunk ? "help-circle-outline" : "arrow-forward"}
          onPress={handleNext}
        />
      </View>

      <Pressable style={styles.fab} onPress={() => setAskOpen(true)}>
        <Ionicons name="mic" size={22} color={colors.white} />
      </Pressable>

      <Modal visible={askOpen} transparent animationType="fade" onRequestClose={() => setAskOpen(false)}>
        <View style={styles.modalBackdrop}>
          <View style={styles.modalCard}>
            <Ionicons name="mic-circle" size={48} color={colors.maroon900} />
            <Text style={styles.modalTitle}>I have a question</Text>
            <Text style={styles.modalBody}>
              Listening… (voice capture wiring goes here — this is a UI stub for now)
            </Text>
            <PrimaryButton label="Done" onPress={() => setAskOpen(false)} />
          </View>
        </View>
      </Modal>
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
    gap: spacing.lg,
  },
  cardHeader: {
    gap: spacing.md,
  },
  chunkTitle: {
    fontFamily: fonts.display,
    fontSize: 22,
    color: colors.ink,
  },
  narration: {
    fontFamily: fonts.body,
    fontSize: 16,
    lineHeight: 24,
    color: colors.ink,
  },
  audioControl: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: colors.white,
    borderRadius: radii.control,
    padding: spacing.sm,
  },
  audioLabel: {
    fontFamily: fonts.bodySemi,
    fontSize: 13,
    color: colors.inkSoft,
  },
  footer: {
    paddingBottom: spacing.md,
  },
  fab: {
    position: "absolute",
    right: spacing.md,
    bottom: 90,
    width: 52,
    height: 52,
    borderRadius: radii.pill,
    backgroundColor: colors.maroon900,
    alignItems: "center",
    justifyContent: "center",
    shadowColor: "#000",
    shadowOpacity: 0.2,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 4 },
    elevation: 4,
  },
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(26,18,16,0.5)",
    alignItems: "center",
    justifyContent: "center",
    padding: spacing.lg,
  },
  modalCard: {
    backgroundColor: colors.white,
    borderRadius: radii.card,
    padding: spacing.lg,
    width: "100%",
    alignItems: "center",
    gap: spacing.sm,
  },
  modalTitle: {
    fontFamily: fonts.displaySemi,
    fontSize: 18,
    color: colors.ink,
  },
  modalBody: {
    fontFamily: fonts.body,
    fontSize: 13,
    color: colors.inkSoft,
    textAlign: "center",
    marginBottom: spacing.sm,
  },
});
