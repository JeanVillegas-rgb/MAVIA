import { API_BASE_URL, DEV_CREDENTIALS } from "./config";
import {
  ApiError,
  type LearningStateRecord,
  type LessonPackage,
  type ModuleSummary,
  type StartLearningResponse,
  type SubmitResponseResult,
} from "./types";

// Logged in once per app session and cached — course endpoints require an
// authenticated request. See DEV_CREDENTIALS in ./config for why.
let authTokenPromise: Promise<string> | null = null;

async function fetchAuthToken(): Promise<string> {
  const response = await fetch(`${API_BASE_URL}/api/auth/login/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(DEV_CREDENTIALS),
  });
  if (!response.ok) {
    throw new ApiError("Dev login failed — check DEV_CREDENTIALS in api/config.ts", response.status);
  }
  const body = await response.json();
  return body.token as string;
}

function ensureAuthToken(): Promise<string> {
  if (!authTokenPromise) {
    authTokenPromise = fetchAuthToken().catch((err) => {
      authTokenPromise = null; // let the next request retry the login
      throw err;
    });
  }
  return authTokenPromise;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await ensureAuthToken();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      Authorization: `Token ${token}`,
    },
    ...init,
  });

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body?.error) message = body.error;
    } catch {
      // response body wasn't JSON — fall back to the generic message above
    }
    throw new ApiError(message, response.status);
  }

  return response.json() as Promise<T>;
}

export function listModules(courseId?: number) {
  const query = courseId ? `?course_id=${courseId}` : "";
  return request<{ modules: ModuleSummary[] }>(`/api/course/modules/${query}`);
}

export function getLessonPackage(nodeId: number) {
  return request<LessonPackage>(`/api/course/lesson-package/${nodeId}/`);
}

export function getLearningState(id: number) {
  return request<LearningStateRecord>(`/api/adaptive/learning-states/${id}/`);
}

export function startLearning(params: { courseId?: number; learnerId?: string } = {}) {
  return request<StartLearningResponse>("/api/adaptive/start/", {
    method: "POST",
    body: JSON.stringify({
      course_id: params.courseId,
      learner_id: params.learnerId ?? "default",
    }),
  });
}

export function submitResponse(params: {
  learningStateId: number;
  questionId: number;
  selectedAnswer: string;
  responseTime?: number;
}) {
  return request<SubmitResponseResult>("/api/adaptive/submit-response/", {
    method: "POST",
    body: JSON.stringify({
      learning_state_id: params.learningStateId,
      question_id: params.questionId,
      selected_answer: params.selectedAnswer,
      response_time: params.responseTime ?? 0,
    }),
  });
}
