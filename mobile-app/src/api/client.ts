import AsyncStorage from "@react-native-async-storage/async-storage";

import { API_BASE_URL } from "@/config";

const TOKEN_KEY = "mavia.authToken";

export async function getToken(): Promise<string | null> {
  return AsyncStorage.getItem(TOKEN_KEY);
}

async function setToken(token: string): Promise<void> {
  await AsyncStorage.setItem(TOKEN_KEY, token);
}

async function clearToken(): Promise<void> {
  await AsyncStorage.removeItem(TOKEN_KEY);
}

async function request(path: string, options: RequestInit = {}) {
  const token = await getToken();
  const headers = new Headers(options.headers as HeadersInit | undefined);
  if (token) {
    headers.set("Authorization", `Token ${token}`);
  }

  let response: Response;
  // Fail fast if the backend is unreachable — without this a black-holed TCP
  // connection leaves screens stuck on their loading state forever.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers,
      signal: controller.signal,
    });
  } catch {
    throw new Error(
      `Can't reach the MAVIA server at ${API_BASE_URL}. Check that the ` +
        "backend is running and reachable from this device."
    );
  } finally {
    clearTimeout(timeout);
  }

  if (!response.ok) {
    let message = `Request failed: ${response.status}`;
    try {
      const data = await response.json();
      if (data?.detail) {
        message = data.detail;
      } else if (Array.isArray(data?.non_field_errors)) {
        message = data.non_field_errors[0];
      } else if (data && typeof data === "object") {
        const first = Object.values(data)[0];
        message = Array.isArray(first) ? String(first[0]) : String(first);
      }
    } catch {
      // keep the status-based message
    }
    throw new Error(message);
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

export type Role = "STUDENT" | "TEACHER" | "ADMIN";

export type User = {
  id: number;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  role: Role;
  is_verified: boolean;
};

export type AuthResponse = { token: string; user: User };

export function login(username: string, password: string): Promise<AuthResponse> {
  return request("/auth/login/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

export type RegisterPayload = {
  username: string;
  email: string;
  password: string;
  first_name?: string;
  last_name?: string;
  role: Role;
};

export function register(
  data: RegisterPayload
): Promise<{ message: string; user: User }> {
  return request("/auth/register/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function verifyEmail(token: string): Promise<{ message: string }> {
  return request(`/auth/verify-email/${encodeURIComponent(token)}/`);
}

export function resendVerification(email: string): Promise<{ message: string }> {
  return request("/auth/resend-verification/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
}

export function fetchMe(): Promise<User> {
  return request("/auth/me/");
}

export async function apiLogout(): Promise<void> {
  await request("/auth/logout/", { method: "POST" });
}

export { setToken, clearToken };

// ---------------------------------------------------------------------------
// Enrolled-student course content (adaptive app). These require the caller to
// be a STUDENT enrolled in the course; the web app has its own teacher-facing
// endpoints.
// ---------------------------------------------------------------------------

export type ApiCourse = {
  id: number;
  title: string;
  description: string;
  module_count: number;
  lesson_count: number;
  track_count: number;
  question_count: number;
  mastery: number | null;
  started: boolean;
  completed: boolean;
};

export type ApiTrack = {
  id: string;
  order: number;
  title: string;
  type: string;
  audio_url: string;
  audio_ready: boolean;
  text: string;
};

// --- learning-path ("path mode") -------------------------------------------
// A topic published with a learning path (backend: learning_path app) walks
// concepts in prerequisite order instead of a flat question list. Present
// only once a topic has been published that way; a lesson/course otherwise
// stays on the plain question list above (ApiLesson.questions).
// See backend/adaptive/PATH_MODE.md for the full ruling this mirrors.

export type Variant = "normal" | "simplified" | "elaborated";

export type ApiStepQuestion = {
  id: number;
  text: string;
  format: "MCQ" | "TF" | string;
  choices: Record<string, string> | null;
  bloom_level: string;
  thinking_order: "LOT" | "HOT" | string;
  difficulty: string;
  category: string;
};

// One chunk of a concept's narration. The content chunker splits an
// oversized passage into "(Part 1 of 2)" pieces; the backend merges those back
// into one concept and lists every piece here, in reading order, each with its
// own recording. Play `parts`, not `audio_url` -- that field only covers the
// whole of `text` when there is a single part.
export type ApiStepVersionPart = { text: string; audio_url: string };

export type ApiStepVersion = {
  text: string;
  audio_url: string;
  parts?: ApiStepVersionPart[];
} | null;

export type ApiStepVersions = {
  normal: ApiStepVersion;
  simplified: ApiStepVersion;
  elaborated: ApiStepVersion;
};

// Another uploaded PDF's own independent telling of this step's concept —
// same shape as the step itself. The engine switches to one of these
// (LearningState.current_chunk) once the representative's own ladder
// (normal/simplified/elaborated) is exhausted; see PATH_MODE.md "chunk
// switching". Rendered by [lessonId].tsx's stepChunk() helper, which resolves
// the active chunk's versions/questions instead of always the representative's.
export type ApiStepAlternate = {
  learning_object_id: number;
  material_id: number;
  material_title: string;
  title: string;
  versions: ApiStepVersions;
  questions: ApiStepQuestion[];
};

export type ApiStep = {
  position: number;
  depth: number;
  concept_id: number;
  title: string;
  section_title: string;
  learning_object_id: number;
  sources: { material_id: number; title: string }[];
  versions: ApiStepVersions;
  // 2 questions per concept, 1 LOT + 1 HOT — never includes correct_answer,
  // per learning_path/HANDOFF.md: answers never reach a student's device.
  questions: ApiStepQuestion[];
  alternates: ApiStepAlternate[];
  prerequisites: number[];
  leads_to: number[];
};

export type ApiLearningState = {
  id: number;
  course: number;
  current_module: number | null;
  current_lesson_node: number | null;
  current_question: number | null;
  current_question_attempts: number;
  // Path-mode fields — null/"normal" while a topic is in the legacy flat mode.
  current_step_position: number | null;
  current_generated_question: number | null;
  remediation_target_position: number | null;
  current_chunk: number | null;
  current_variant: Variant;
  mastery: number;
  attempts: number;
  completed: boolean;
  started_at: string;
  updated_at: string;
};

// -----------------------------------------------------------------------

export type ApiLesson = {
  id: number;
  title: string;
  module_title?: string;
  published: boolean;
  tracks: ApiTrack[];
  questions: {
    id: number;
    order: number;
    prompt: string;
    question_type: string;
    choices: string[];
    correct_answer: string;
  }[];
  has_questions: boolean;
  track_count: number;
  question_count: number;
};

// The API returns audio_url as a server-absolute path ("/media/..."). Turn it
// into a full URL the device can fetch by borrowing API_BASE_URL's origin.
export function resolveMediaUrl(path: string): string {
  if (!path) return "";
  if (/^https?:\/\//i.test(path)) return path;
  const origin = API_BASE_URL.replace(/\/api\/?$/, "");
  return `${origin}${path.startsWith("/") ? "" : "/"}${path}`;
}

export function fetchMyCourses(): Promise<ApiCourse[]> {
  return request("/adaptive/my-courses/");
}

export function fetchMyCourseLessons(courseId: number | string): Promise<ApiLesson[]> {
  return request(`/adaptive/my-courses/${courseId}/lessons/`);
}

export function fetchLessonPackage(lessonId: number | string): Promise<ApiLesson> {
  return request(`/adaptive/lessons/${lessonId}/`);
}

export type ApiStartResult = {
  learning_state: ApiLearningState;
  lesson: ApiLesson | null;
  // Present only when this student's next content is a published-path
  // concept rather than the plain question list on `lesson`.
  current_step: ApiStep | null;
};

// `lessonNodeId` is the topic the player has open. The engine's cursor is
// course-wide, so without it the two can disagree -- the student taps one
// lesson and is served another's concept, or a finished course leaves them
// with no assigned question and the player quietly falls back to a flat
// playlist with no questions at all.
export function startLearning(
  courseId: number | string,
  lessonNodeId?: number | string
): Promise<ApiStartResult> {
  return request("/adaptive/start/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      course_id: courseId,
      ...(lessonNodeId != null ? { lesson_node_id: Number(lessonNodeId) } : {}),
    }),
  });
}

export type ApiSubmitResult = {
  is_correct: boolean;
  mastery: number;
  completed: boolean;
  next_module: number | null;
  next_lesson_node: number | null;
  next_question: number | null;
  // Path-mode only (undefined in legacy mode):
  next_step_position?: number | null;
  remediation_target_position?: number | null;
  current_chunk?: number | null;
  current_variant?: Variant;
  current_step: ApiStep | null;
  lesson: ApiLesson | null;
};

export function submitResponse(params: {
  learning_state_id: number;
  question_id: number;
  selected_answer: string;
}): Promise<ApiSubmitResult> {
  return request("/adaptive/submit-response/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}
