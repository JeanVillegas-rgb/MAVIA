import React, { useEffect, useState } from "react";
import { FlatList, StyleSheet, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { SafeAreaView } from "react-native-safe-area-context";

import IconButton from "@/components/IconButton";
import GradientTile from "@/components/GradientTile";
import ListRow from "@/components/ListRow";
import EmptyState from "@/components/EmptyState";
import { Course, Lesson, fetchCourse, fetchLessons } from "@/data/library";
import { colors, gradients, radii, shadow, spacing } from "@/theme";

export default function CourseDetailScreen() {
  const router = useRouter();
  const { courseId } = useLocalSearchParams<{ courseId: string }>();

  const [course, setCourse] = useState<Course | null>(null);
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([fetchCourse(courseId), fetchLessons(courseId)]).then(
      ([courseData, lessonData]) => {
        if (cancelled) return;
        setCourse(courseData);
        setLessons(lessonData);
        setLoading(false);
      }
    );
    return () => {
      cancelled = true;
    };
  }, [courseId]);

  const title = course?.title ?? "Course";
  const subtitle = course?.subtitle ?? "";

  function openLesson(lesson: Lesson) {
    router.push(`/home/${courseId}/${lesson.id}`);
  }

  return (
    <View style={styles.screen}>
      <LinearGradient colors={gradients.hero} style={styles.hero}>
        <SafeAreaView edges={["top"]}>
          <View style={styles.heroTopRow}>
            <IconButton
              icon="chevron-back"
              accessibilityLabel="Go back to courses"
              variant="light"
              onPress={() => router.back()}
            />
            <IconButton
              icon="ellipsis-horizontal"
              accessibilityLabel="More options"
              variant="light"
              disabled
            />
          </View>

          <Text style={styles.heroTitle} accessibilityRole="header">
            {title}
          </Text>
          {subtitle ? <Text style={styles.heroSubtitle}>{subtitle}</Text> : null}
        </SafeAreaView>

        <IconButton
          icon="play"
          accessibilityLabel={
            lessons.length > 0 ? `Play ${title} from the beginning` : "No lessons to play yet"
          }
          variant="light"
          size={56}
          disabled={lessons.length === 0}
          onPress={() => lessons[0] && openLesson(lessons[0])}
          style={styles.heroPlay}
        />
      </LinearGradient>

      <View style={styles.body}>
        <Text style={styles.sectionTitle} accessibilityRole="header">
          Lessons
        </Text>

        {!loading && lessons.length === 0 ? (
          <EmptyState
            icon="musical-notes-outline"
            title="No lessons yet"
            body="Lessons your teacher publishes for this course will show up here."
          />
        ) : (
          <FlatList
            data={lessons}
            keyExtractor={(item) => item.id}
            contentContainerStyle={styles.list}
            renderItem={({ item }) => (
              <ListRow
                title={item.title}
                subtitle={item.durationLabel}
                leading={<GradientTile size={44} icon="headset" />}
                trailing={
                  <IconButton
                    icon="play"
                    accessibilityLabel={`Play ${item.title}`}
                    variant="ghost"
                    size={32}
                    onPress={() => openLesson(item)}
                  />
                }
                onPress={() => openLesson(item)}
                accessibilityLabel={`${item.title}, ${item.durationLabel}`}
                accessibilityHint="Double tap to play"
              />
            )}
          />
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.page },
  hero: {
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.xl + 24,
    borderBottomLeftRadius: radii.lg,
    borderBottomRightRadius: radii.lg,
  },
  heroTopRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: spacing.sm,
  },
  heroTitle: {
    marginTop: spacing.lg,
    fontSize: 26,
    fontWeight: "800",
    color: colors.white,
  },
  heroSubtitle: { marginTop: 4, fontSize: 14, color: "rgba(255,255,255,0.78)" },
  heroPlay: {
    position: "absolute",
    right: spacing.lg,
    bottom: -24,
    ...shadow.card,
  },
  body: {
    flex: 1,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.xl + spacing.sm,
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: "800",
    color: colors.brand800,
    marginBottom: spacing.sm,
  },
  list: { gap: spacing.sm, paddingBottom: spacing.xl },
});
