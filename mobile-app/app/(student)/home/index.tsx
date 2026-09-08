import React, { useCallback, useState } from "react";
import {
  FlatList,
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
import { Course, fetchContinueLearning, fetchCourses } from "@/data/library";
import { colors, radii, spacing } from "@/theme";

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

  function openCourse(course: Course) {
    router.push(`/home/${course.id}`);
  }

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

      <Text style={styles.sectionTitle} accessibilityRole="header">
        Continue learning
      </Text>
      {continueLearning.length === 0 ? (
        <EmptyState
          icon="play-circle-outline"
          title="Nothing in progress yet"
          body="Courses you start will show up here."
        />
      ) : (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.continueRow}
        >
          {continueLearning.map((course) => (
            <View key={course.id} style={styles.continueCard}>
              <GradientTile size={128} radius={radii.md} />
              <Text style={styles.continueTitle} numberOfLines={1}>
                {course.title}
              </Text>
              <Text style={styles.continueSubtitle} numberOfLines={1}>
                {course.subtitle}
              </Text>
            </View>
          ))}
        </ScrollView>
      )}

      <Text style={styles.sectionTitle} accessibilityRole="header">
        All courses
      </Text>
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
            <View style={columns > 1 ? styles.gridItem : undefined}>
              <ListRow
                title={item.title}
                subtitle={`${item.subtitle} · ${item.lessonCount} lessons`}
                leading={<GradientTile size={44} />}
                onPress={() => openCourse(item)}
                accessibilityLabel={`${item.title}. ${item.subtitle}. ${item.lessonCount} lessons. ${item.progressPercent} percent complete.`}
              />
            </View>
          )}
        />
      )}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  heading: { fontSize: 24, fontWeight: "800", color: colors.ink, marginBottom: spacing.md },
  searchField: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    backgroundColor: colors.panel,
    borderRadius: radii.pill,
    paddingHorizontal: spacing.md,
    height: 44,
    marginBottom: spacing.lg,
  },
  searchInput: { flex: 1, fontSize: 15, color: colors.ink, height: "100%" },
  sectionTitle: {
    fontSize: 15,
    fontWeight: "800",
    color: colors.brand800,
    marginBottom: spacing.sm,
  },
  continueRow: { gap: spacing.md, paddingBottom: spacing.lg },
  continueCard: { width: 128, gap: 6 },
  continueTitle: { fontSize: 13, fontWeight: "700", color: colors.ink },
  continueSubtitle: { fontSize: 11, color: colors.muted },
  list: { gap: spacing.sm, paddingBottom: spacing.xl },
  columnWrap: { gap: spacing.sm },
  gridItem: { flex: 1 },
});
