import AsyncStorage from "@react-native-async-storage/async-storage";

import { API_BASE_URL } from "@/config";

import type {
  ApiAnswerResult,
  ApiCommand,
  ApiCourse,
  ApiCourseOutline,
  ApiTopicPackage,
} from "./types";

export * from "./types";

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

// An error the server answered with, carrying its HTTP status so a screen can
// tell "refused" (e.g. 409: you're not where you think you are) from "broken".
export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

const DEFAULT_TIMEOUT_MS = 10000;

async function request(path: string, options: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const token = await getToken();
  const headers = new Headers(options.headers as HeadersInit | undefined);
  if (token) {
    headers.set("Authorization", `Token ${token}`);
  }

  let response: Response;
  // Fail fast if the backend is unreachable — without this a black-holed TCP
  // connection leaves screens stuck on their loading state forever.
  const controller = new AbortController();
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers,
      signal: controller.signal,
    });
  } catch {
    // A server that answered too slowly is not an unreachable one: saying
    // "can't reach" sent us chasing the network when it was only busy.
    throw new Error(
      timedOut
        ? "The MAVIA server is taking too long to respond. Please try again in a moment."
        : `Can't reach the MAVIA server at ${API_BASE_URL}. Check that the ` +
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
    throw new ApiError(message, response.status);
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
// The student's audio course (backend: mobile_course_package). Every call
// requires a STUDENT enrolled in the course. Answers are graded and the next
// step is decided on the server (backend: adaptive) -- the phone never holds
// an answer key, it only follows the command that comes back.
// ---------------------------------------------------------------------------

// The API returns audio_url as a server-absolute path ("/media/..."). Turn it
// into a full URL the device can fetch by borrowing API_BASE_URL's origin.
export function resolveMediaUrl(path: string): string {
  if (!path) return "";
  if (/^https?:\/\//i.test(path)) return path;
  const origin = API_BASE_URL.replace(/\/api\/?$/, "");
  return `${origin}${path.startsWith("/") ? "" : "/"}${path}`;
}

export function fetchMyCourses(): Promise<ApiCourse[]> {
  return request("/mobile/my-courses/");
}

export function fetchCourseOutline(courseId: number | string): Promise<ApiCourseOutline> {
  return request(`/mobile/courses/${courseId}/`);
}

// The first open of a topic after the teacher (re)publishes it builds its
// package on the server, which loads language models and can take well over
// the default timeout. Every open after that is instant.
const PACKAGE_BUILD_TIMEOUT_MS = 120000;

export function fetchTopicPackage(topicId: number | string): Promise<ApiTopicPackage> {
  return request(`/mobile/topics/${topicId}/`, {}, PACKAGE_BUILD_TIMEOUT_MS);
}

export function submitAnswer(params: {
  topicId: number | string;
  questionId: number;
  selectedAnswer: string;
}): Promise<ApiAnswerResult> {
  return request("/mobile/answers/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      topic_id: Number(params.topicId),
      question_id: params.questionId,
      selected_answer: params.selectedAnswer,
    }),
  });
}

// After a listen-only step has been heard: move on. Refused (409) if the step
// still has questions to answer.
export function continueTopic(topicId: number | string): Promise<{ next: ApiCommand }> {
  return request(`/mobile/topics/${topicId}/continue/`, { method: "POST" });
}
