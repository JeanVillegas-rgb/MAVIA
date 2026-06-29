const API_BASE = "/api";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  if (!response.ok) {
    let message = `Request failed: ${response.status}`;
    try {
      const data = await response.json();
      message = data.detail || JSON.stringify(data);
    } catch {
      const text = await response.text();
      if (text) message = text;
    }
    throw new Error(message);
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

export function fetchCourses() {
  return request("/courses/");
}

export function fetchCourse(id) {
  return request(`/courses/${id}/`);
}

export function createCourse(data) {
  return request("/courses/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function uploadCourseOutline(courseId, formData) {
  return request(`/courses/${courseId}/upload-outline/`, {
    method: "POST",
    body: formData,
  });
}

export function uploadLessonToNode(courseId, nodeId, formData) {
  return request(`/courses/${courseId}/nodes/${nodeId}/upload-lesson/`, {
    method: "POST",
    body: formData,
  });
}

export function fetchPublishedLessons(courseId) {
  return request(`/courses/${courseId}/published/`);
}

export function fetchLessons() {
  return request("/lessons/");
}

export function fetchLesson(id) {
  return request(`/lessons/${id}/`);
}

export function updateLessonScript(id, modules) {
  return request(`/lessons/${id}/update-script/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ modules }),
  });
}

export function approveLessonScript(id) {
  return request(`/lessons/${id}/approve-script/`, { method: "POST" });
}

export function publishLesson(id) {
  return request(`/lessons/${id}/publish/`, { method: "POST" });
}

export function reprocessLesson(id) {
  return request(`/lessons/${id}/reprocess/`, { method: "POST" });
}

export function deleteLesson(id) {
  return request(`/lessons/${id}/`, { method: "DELETE" });
}

export function deleteCourse(id) {
  return request(`/courses/${id}/`, { method: "DELETE" });
}
