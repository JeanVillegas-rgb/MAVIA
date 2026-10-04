import React, { useCallback, useMemo, useState } from "react";
import {
  FlatList,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";

import ScreenContainer from "@/components/ScreenContainer";
import GradientTile from "@/components/GradientTile";
import ListRow from "@/components/ListRow";
import EmptyState from "@/components/EmptyState";
import { useResponsive } from "@/hooks/useResponsive";
import { useNarration } from "@/hooks/useNarration";
import { useListPicker } from "@/nav/useListPicker";
import { useGuideBusy } from "@/guide/GuideActivity";
import { Course, fetchContinueLearning, fetchCourses } from "@/data/library";
import { colors, radii, shadow, spacing } from "@/theme";

export default function CourseListScreen() {
  const router = useRouter();
  const { columns } = useResponsive();

  const [query, setQuery] = useState("");
  const [courses, setCourses] = useState<Course[]>([]);
  const [continueLearning, setContinueLearning] = useState<Course[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    const [all, inProgress] = await Promise.all([
      fetchCourses(),
      fetchContinueLearning(),
    ]);
    setCourses(all);
    setContinueLearning(inProgress);
    setLoading(false);
  }, []);

  // Re-check for content whenever the tab regains focus (e.g. after a
  // future "enroll in a course" flow), not just on first mount.
  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const filtered = query.trim()
    ? courses.filter((course) =>
        course.title.toLowerCase().includes(query.trim().toLowerCase())
      )
    : courses;

  // "Continue learning" repeats the list below whenever every course is
  // already in progress, which on a one-course account meant the same card
  // twice on an otherwise empty screen. It earns its place only when it is a
  // shortcut PAST something.
  const showContinue = useMemo(
    () => continueLearning.length > 0 && filtered.length > continueLearning.length,
    [continueLearning.length, filtered.length]
  );

  const openCourse = useCallback(
    (course: Course) => {
      router.push(`/home/${course.id}`);
    },
    [router]
  );

  // Choosing by ear: the courses are read out four at a time and picked by
  // letter or by keypad. Held back until the list has actually loaded, so the
  // prompt never announces an empty list and then has to correct itself.
  // Searching is a sighted path, so typing a query turns the reading off
  // rather than having it re-read on every keystroke.
  const narration = useNarration();
  // Silent while the guide or a practice drill is speaking: one voice, and
  // the guide owns it (see GuideActivity).
  const guideBusy = useGuideBusy();
  const pickerReady = !loading && filtered.length > 0 && query.trim() === "" && !guideBusy;
  useListPicker<Course>({
    items: filtered,
    labelOf: (course) => course.title,
    question: "Which course would you like?",
    narration,
    onPick: openCourse,
    enabled: pickerReady,
  });

  return (
    <ScreenContainer>
      <Text style={styles.heading} accessibilityRole="header">
        Courses
      </Text>

      <View style={styles.searchField}>
        <Ionicons name="search" size={18} color={colors.faint} />
        <TextInput
          value={query}
          onChangeText={setQuery}
          placeholder="Search courses"
          placeholderTextColor={colors.faint}
          style={styles.searchInput}
          accessibilityRole="search"
          accessibilityLabel="Search courses"
          returnKeyType="search"
        />
      </View>

      {showContinue && (
        <>
          <Text style={styles.sectionTitle} accessibilityRole="header">
            Continue learning
          </Text>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.continueRow}
          >
            {continueLearning.map((course) => (
              <Pressable
                key={course.id}
                style={styles.continueCard}
                onPress={() => openCourse(course)}
                accessibilityRole="button"
                accessibilityLabel={`Continue ${course.title}`}
              >
                <GradientTile size={148} radius={radii.md} />
                <Text style={styles.continueTitle} numberOfLines={2}>
                  {course.title}
                </Text>
                <Text style={styles.continueSubtitle} numberOfLines={1}>
                  {course.subtitle}
                </Text>
              </Pressable>
            ))}
          </ScrollView>
        </>
      )}
      {!loading && filtered.length === 0 ? (
        <EmptyState
          icon="library-outline"
          title={query ? "No courses match your search" : "No courses yet"}
          body={query ? undefined : "Your teacher hasn't added any courses yet."}
        />
      ) : (
        <FlatList
          data={filtered}
          keyExtractor={(item) => item.id}
          scrollEnabled={false}
          numColumns={columns}
          key={columns}
          columnWrapperStyle={columns > 1 ? styles.columnWrap : undefined}
          contentContainerStyle={styles.list}
          renderItem={({ item }) => (
            <Pressable
              style={styles.courseCard}
              onPress={() => openCourse(item)}
              accessibilityRole="button"
              accessibilityLabel={`${item.title}. ${item.subtitle}`}
            >
              <View style={styles.courseBanner}>
                <Ionicons
                  name="school"
                  size={150}
                  color="rgba(255,255,255,0.14)"
                  style={styles.courseGlyph}
                />
                <View style={styles.coursePill}>
                  <Text style={styles.coursePillText}>
                    {item.lessonCount} lesson{item.lessonCount === 1 ? "" : "s"}
                  </Text>
                </View>
              </View>
              <View style={styles.courseBody}>
                <Text style={styles.courseTitle} numberOfLines={2}>
                  {item.title}
                </Text>
                <Text style={styles.courseMeta} numberOfLines={1}>
                  {item.subtitle}
                </Text>
                <View style={styles.progressTrack}>
                  <View
                    style={[
                      styles.progressFill,
                      { width: `${Math.max(4, Math.min(100, item.progressPercent))}%` },
                    ]}
                  />
                </View>
                <Text style={styles.progressLabel}>
                  {item.progressPercent > 0 ? `${item.progressPercent}% complete` : "Not started"}
                </Text>
              </View>
            </Pressable>
          )}
        />
      )}
    </ScreenContainer>
  );
}


const styles = StyleSheet.create({
  heading: {
    fontSize: 34,
    fontWeight: "800",
    color: colors.ink,
    letterSpacing: -0.8,
    marginBottom: spacing.lg,
  },
  searchField: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: colors.panel,
    borderRadius: radii.pill,
    paddingHorizontal: spacing.lg,
    height: 54,
    marginBottom: spacing.xl,
  },
  searchInput: { flex: 1, fontSize: 16, color: colors.ink, height: "100%" },
  // A section heading is a label, not a second title: small, spaced and quiet,
  // so the one big heading above stays the only loud thing on the screen.
  sectionTitle: {
    fontSize: 12,
    fontWeight: "800",
    letterSpacing: 1.4,
    textTransform: "uppercase",
    color: colors.faint,
    marginBottom: spacing.md,
  },
  continueRow: { gap: spacing.md, paddingBottom: spacing.xl },
  continueCard: { width: 148, gap: 10 },
  continueTitle: { fontSize: 15, fontWeight: "700", color: colors.ink },
  continueSubtitle: { fontSize: 13, color: colors.muted },
  list: { gap: spacing.md, paddingBottom: spacing.xl },
  // A course is a card with its own banner rather than a thin row beneath a
  // large loose square: the block of colour belongs to the thing it names.
  courseCard: {
    backgroundColor: colors.surface,
    borderRadius: radii.md,
    overflow: "hidden",
    ...shadow.card,
  },
  courseBanner: {
    height: 132,
    backgroundColor: colors.brand600,
    justifyContent: "flex-end",
    padding: spacing.lg,
    overflow: "hidden",
  },
  // A watermark rather than an illustration: it gives the block something to
  // be without pretending we have artwork we do not have.
  // Bled off ONE edge, not cropped by a corner: a shape leaving the frame
  // reads as deliberate where a shape clipped twice reads as a bug.
  courseGlyph: { position: "absolute", right: -26, bottom: -30 },
  coursePill: {
    alignSelf: "flex-start",
    backgroundColor: "rgba(255,255,255,0.22)",
    borderRadius: radii.pill,
    paddingHorizontal: spacing.md,
    paddingVertical: 5,
  },
  coursePillText: { fontSize: 13, fontWeight: "700", color: colors.white },
  courseBody: { padding: spacing.lg, gap: 8 },
  courseTitle: { fontSize: 22, fontWeight: "800", letterSpacing: -0.4, color: colors.ink },
  courseMeta: { fontSize: 14, color: colors.muted },
  progressTrack: {
    height: 8,
    borderRadius: radii.pill,
    backgroundColor: colors.panel,
    overflow: "hidden",
    marginTop: 6,
  },
  progressFill: { height: "100%", borderRadius: radii.pill, backgroundColor: colors.brand600 },
  progressLabel: { fontSize: 13, fontWeight: "600", color: colors.faint },
  columnWrap: { gap: spacing.md },
  gridItem: { flex: 1 },
});
