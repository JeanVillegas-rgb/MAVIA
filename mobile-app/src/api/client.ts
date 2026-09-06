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
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  } catch {
    throw new Error(
      `Can't reach the MAVIA server at ${API_BASE_URL}. Check that the ` +
        "backend is running and reachable from this device."
    );
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

export function fetchMyCourses(): Promise<ApiCourse[]> {
  return request("/adaptive/my-courses/");
}

export function fetchMyCourseLessons(courseId: number | string): Promise<ApiLesson[]> {
  return request(`/adaptive/my-courses/${courseId}/lessons/`);
}

export function fetchLessonPackage(lessonId: number | string): Promise<ApiLesson> {
  return request(`/adaptive/lessons/${lessonId}/`);
}

export function startLearning(courseId: number | string) {
  return request("/adaptive/start/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ course_id: courseId }),
  });
}

export function submitResponse(params: {
  learning_state_id: number;
  question_id: number;
  selected_answer: string;
}) {
  return request("/adaptive/submit-response/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}
