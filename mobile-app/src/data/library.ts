// Course/topic library for the enrolled student. Screens call these fetchers;
// they read the mobile_course_package API. The screen-facing shapes (Course,
// Lesson) are kept stable so the list screens didn't have to change.
//
// A "lesson" on screen is a published topic of the course outline
// (Course -> Module -> Topic); opening one plays its audio course package.

import { ApiCourse, fetchCourseOutline, fetchMyCourses } from "@/api/client";

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
  // The module the topic belongs to, shown under its title.
  durationLabel: string;
  durationSeconds: number;
};

function toCourse(api: ApiCourse): Course {
  const total = api.topic_count;
  return {
    id: String(api.id),
    title: api.title,
    subtitle: `${total} topic${total === 1 ? "" : "s"}`,
    lessonCount: total,
    progressPercent: total ? Math.round((api.completed_topic_count / total) * 100) : 0,
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
    const outline = await fetchCourseOutline(courseId);
    return outline.modules.flatMap((module) =>
      module.topics.map((topic) => ({
        id: String(topic.id),
        courseId,
        title: topic.title,
        durationLabel: module.title,
        durationSeconds: 0,
      }))
    );
  } catch {
    return [];
  }
}

// Courses the student has begun but not finished.
export async function fetchContinueLearning(): Promise<Course[]> {
  try {
    const rows = await fetchMyCourses();
    return rows
      .filter((row) => row.completed_topic_count > 0 && row.completed_topic_count < row.topic_count)
      .map(toCourse);
  } catch {
    return [];
  }
}
