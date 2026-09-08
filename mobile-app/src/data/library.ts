// Course/lesson library for the enrolled student. Screens call these
// fetchers; they now hit the real Django adaptive API instead of returning
// empty arrays. The screen-facing shapes (Course, Lesson) are kept stable so
// no screen had to change.

import {
  ApiCourse,
  ApiLesson,
  fetchLessonPackage,
  fetchMyCourseLessons,
  fetchMyCourses,
} from "@/api/client";

export type Course = {
  id: string;
  title: string;
  subtitle: string;
  lessonCount: number;
  progressPercent: number;
};

export type Lesson = {
  id: string;
  courseId: string;
  title: string;
  durationLabel: string;
  durationSeconds: number;
};

function toCourse(api: ApiCourse): Course {
  return {
    id: String(api.id),
    title: api.title,
    subtitle:
      api.description?.trim() ||
      `${api.module_count} module${api.module_count === 1 ? "" : "s"}`,
    lessonCount: api.lesson_count,
    progressPercent: api.mastery == null ? 0 : Math.round(api.mastery * 100),
  };
}

function toLesson(courseId: string, api: ApiLesson): Lesson {
  return {
    id: String(api.id),
    courseId,
    title: api.title,
    durationLabel: `${api.track_count} track${api.track_count === 1 ? "" : "s"}`,
    durationSeconds: 0,
  };
}

export async function fetchCourses(): Promise<Course[]> {
  try {
    const rows = await fetchMyCourses();
    return rows.map(toCourse);
  } catch {
    return [];
  }
}

export async function fetchCourse(courseId: string): Promise<Course | null> {
  const courses = await fetchCourses();
  return courses.find((course) => course.id === courseId) ?? null;
}

export async function fetchLessons(courseId: string): Promise<Lesson[]> {
  try {
    const rows = await fetchMyCourseLessons(courseId);
    return rows.map((lesson) => toLesson(courseId, lesson));
  } catch {
    return [];
  }
}

export async function fetchLesson(
  courseId: string,
  lessonId: string
): Promise<Lesson | null> {
  try {
    const pkg = await fetchLessonPackage(lessonId);
    return toLesson(courseId, pkg);
  } catch {
    return null;
  }
}

export async function fetchContinueLearning(): Promise<Course[]> {
  try {
    const rows = await fetchMyCourses();
    return rows.filter((row) => row.started && !row.completed).map(toCourse);
  } catch {
    return [];
  }
}
