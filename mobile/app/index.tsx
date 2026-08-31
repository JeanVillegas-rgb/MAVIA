import React, { useEffect, useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import TopNav from "@/components/TopNav";
import BottomNav from "@/components/BottomNav";
import LessonCard from "@/components/LessonCard";
import PrimaryButton from "@/components/PrimaryButton";
import { fetchModules } from "@/data/api";
import { CourseModuleSummary } from "@/data/types";
import { colors, fonts, radii, spacing } from "@/theme";

const EMOJI: Record<string, string> = {
  m1: "🌱",
  m2: "🦒",
  m3: "⛅",
  m4: "🌍",
};

export default function Dashboard() {
  const router = useRouter();
  const [modules, setModules] = useState<CourseModuleSummary[]>([]);

  useEffect(() => {
    fetchModules().then(setModules);
  }, []);

  return (
    <View style={styles.screen}>
      <ScrollView contentContainerStyle={styles.scroll}>
        <TopNav />

        <Text style={styles.heading}>Lessons:</Text>

        <View style={styles.grid}>
          <View style={styles.column}>
            {modules
              .filter((_, i) => i % 2 === 0)
              .map((m) => (
                <LessonCard
                  key={m.id}
                  title={m.title}
                  emoji={EMOJI[m.id]}
                  onPress={() => router.push(`/lesson/${m.lessonNodeId}`)}
                />
              ))}
          </View>
          <View style={styles.column}>
            {modules
              .filter((_, i) => i % 2 === 1)
              .map((m) => (
                <LessonCard
                  key={m.id}
                  title={m.title}
                  emoji={EMOJI[m.id]}
                  onPress={() => router.push(`/lesson/${m.lessonNodeId}`)}
                />
              ))}
          </View>

          <View style={styles.storyCard}>
            <Text style={styles.storyTitle}>Narrative Story{"\n"}Line:</Text>
            <View style={styles.storyImage} />
            <PrimaryButton label="Explore" icon="play" onPress={() => router.push("/lesson/ln2")} />
          </View>
        </View>
      </ScrollView>

      <LinearGradient colors={["transparent", colors.blush]} style={styles.bottomFade} pointerEvents="none" />

      <View style={styles.bottomNavWrap}>
        <BottomNav />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.cream,
  },
  scroll: {
    padding: spacing.md,
    paddingBottom: 120,
    gap: spacing.lg,
  },
  heading: {
    fontFamily: fonts.display,
    fontSize: 28,
    color: colors.ink,
    marginTop: spacing.md,
  },
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.md,
  },
  column: {
    flexBasis: "47%",
    gap: spacing.md,
  },
  storyCard: {
    flexBasis: "100%",
    backgroundColor: colors.card,
    borderRadius: radii.card,
    padding: spacing.md,
    gap: spacing.md,
  },
  storyTitle: {
    fontFamily: fonts.display,
    fontSize: 20,
    color: colors.ink,
  },
  storyImage: {
    height: 160,
    borderRadius: radii.control,
    backgroundColor: colors.blush,
  },
  bottomFade: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    height: 100,
  },
  bottomNavWrap: {
    position: "absolute",
    left: spacing.md,
    right: spacing.md,
    bottom: spacing.md,
  },
});
