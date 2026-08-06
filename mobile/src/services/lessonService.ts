import api from "./api";

import {
  CourseModule,
  StartLearningResult,
} from "../models/LessonPackage";

export async function fetchLesson(
  nodeId: number
): Promise<CourseModule> {
  const response = await api.get(
    `course/lesson-package/${nodeId}/`
  );

  return response.data;
}

export async function startLearning(
  courseId?: number,
  learnerId = "default"
): Promise<StartLearningResult> {
  const response = await api.post(
    "adaptive/start/",
    {
      learner_id: learnerId,
      ...(courseId ? { course_id: courseId } : {}),
    }
  );

  return response.data;
}