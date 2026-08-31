import { useRouter } from "expo-router";
import { useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, useWindowDimensions, View } from "react-native";

import { listModules, startLearning } from "@/api/client";
import type { ModuleSummary, StartLearningResponse } from "@/api/types";
import { ModuleCard } from "@/components/ui/module-card";
import { NarrativeCard } from "@/components/ui/narrative-card";
import { ScreenFrame } from "@/components/ui/screen-frame";
import { colors, fonts, spacing } from "@/theme";

const WIDE_BREAKPOINT = 700;
const MODULE_EMOJIS = ["\u{1FAA8}", "\u{1F33F}", "\u{26C5}", "\u{1F30D}", "\u{1F9EA}", "\u{1F52C}"];

export default function LessonsScreen() {
  const router = useRouter();
  const { width } = useWindowDimensions();
  const isWide = width >= WIDE_BREAKPOINT;

  const [modules, setModules] = useState<ModuleSummary[] | null>(null);
  const [learningState, setLearningState] = useState<StartLearningResponse | null>(null);
  const [selectedModuleId, setSelectedModuleId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    // Sequential, not Promise.all: both calls sync the course outline in a
    // write transaction, and SQLite doesn't handle concurrent writers well.
    startLearning({ learnerId: "default" })
      .then((startResult) =>
        listModules().then((moduleResult) => {
          if (cancelled) return;
          setLearningState(startResult);
          setModules(moduleResult.modules);
          setSelectedModuleId(startResult.lesson.module.id);
        }),
      )
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Couldn't load your lessons.");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <ScreenFrame eyebrow="Modules">
        <Text style={styles.heading}>Lessons:</Text>
        <Text style={styles.errorText}>{error}</Text>
      </ScreenFrame>
    );
  }

  if (!modules || !learningState) {
    return (
      <ScreenFrame eyebrow="Modules">
        <View style={styles.loadingWrap}>
          <ActivityIndicator color={colors.maroon900} size="large" />
        </View>
      </ScreenFrame>
    );
  }

  const currentModuleId = learningState.lesson.module.id;
  const selectedModule = modules.find((module) => module.id === selectedModuleId) ?? modules[0];
  const isCurrentModuleSelected = selectedModule?.id === currentModuleId;
  const previewNode = isCurrentModuleSelected
    ? learningState.lesson.lesson_node
    : selectedModule?.lesson_nodes[0];

  const goToLesson = () => {
    if (!previewNode) return;
    if (isCurrentModuleSelected) {
      router.push({
        pathname: "/lesson/[id]",
        params: { id: String(previewNode.id), learningStateId: String(learningState.learning_state_id) },
      });
    } else {
      router.push({ pathname: "/lesson/[id]", params: { id: String(previewNode.id) } });
    }
  };

  return (
    <ScreenFrame eyebrow="Modules">
      <Text style={styles.heading}>Lessons:</Text>
      <View style={[styles.layout, isWide && styles.layoutWide]}>
        <View style={[styles.grid, isWide && styles.gridWide]}>
          {modules.map((module, index) => (
            <ModuleCard
              key={module.id}
              title={module.title}
              emoji={MODULE_EMOJIS[index % MODULE_EMOJIS.length]}
              selected={module.id === selectedModuleId}
              locked={module.lesson_nodes.length === 0}
              onPress={() => setSelectedModuleId(module.id)}
            />
          ))}
        </View>
        <View style={isWide ? styles.featuredWide : styles.featured}>
          {previewNode ? (
            <NarrativeCard
              title={`${previewNode.title}:`}
              selected={isCurrentModuleSelected}
              onPress={goToLesson}
            />
          ) : (
            <View style={styles.emptyPreview}>
              <Text style={styles.emptyPreviewText}>
                This module isn&apos;t available yet — check back soon.
              </Text>
            </View>
          )}
        </View>
      </View>
    </ScreenFrame>
  );
}

const styles = StyleSheet.create({
  heading: {
    fontFamily: fonts.display,
    fontSize: 24,
    color: colors.ink,
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
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.sm,
  },
  gridWide: {
    flex: 1,
  },
  featured: {
    width: "100%",
  },
  featuredWide: {
    flex: 1,
  },
  emptyPreview: {
    padding: spacing.lg,
    alignItems: "center",
    justifyContent: "center",
  },
  emptyPreviewText: {
    fontFamily: fonts.body,
    fontSize: 14,
    color: colors.inkSoft,
    textAlign: "center",
  },
});
