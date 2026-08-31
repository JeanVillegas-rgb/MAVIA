import { CourseModuleSummary, LessonPackage } from "./types";
import { LESSON_PACKAGES, MODULES } from "./mockData";

const MOCK_DELAY = 250;

function delay<T>(value: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), MOCK_DELAY));
}

/**
 * GET /courses/:courseId/modules  (roughly)
 * Swap this body for a real fetch() once the DRF endpoint exists —
 * screens only depend on the CourseModuleSummary[] shape, not on how
 * it's fetched.
 */
export async function fetchModules(): Promise<CourseModuleSummary[]> {
  return delay(MODULES);
}

/**
 * GET /lesson-nodes/:lessonNodeId/package
 * Should return chunks (with NORMAL pulled live from generated_json,
 * ELABORATED/SIMPLIFIED from LessonObjectVariant) + ordered questions
 * (from ModuleQuestion -> GeneratedQuestion).
 */
export async function fetchLessonPackage(lessonNodeId: string): Promise<LessonPackage | null> {
  return delay(LESSON_PACKAGES[lessonNodeId] ?? null);
}

/**
 * POST /student-responses  (StudentResponse creation)
 * Fire-and-forget from the mobile app's point of view — real version
 * should also return updated LearningState (mastery, next variant/bloom)
 * so the app can adapt in real time.
 */
export async function submitAnswer(params: {
  learnerId: string;
  questionId: string;
  selectedAnswer: string;
  isCorrect: boolean;
  responseTimeSec: number;
}): Promise<{ ok: true }> {
  console.log("[mock submitAnswer]", params);
  return delay({ ok: true } as const);
}
