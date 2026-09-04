const API_BASE = "/api";

async function request(path, options = {}) {
  const token = localStorage.getItem("authToken");
  const headers = new Headers(options.headers || {});
  if (token) {
    headers.set("Authorization", `Token ${token}`);
  }

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  } catch (error) {
    throw new Error(
      "API server unreachable. Start the Django backend at http://127.0.0.1:8000, then refresh this page."
    );
  }

  if (!response.ok) {
    let message = `Request failed: ${response.status}`;
    const responseForText = response.clone();
    try {
      const data = await response.json();
      message = data.detail || JSON.stringify(data);
    } catch {
      try {
        const text = await responseForText.text();
        if (text && !text.trim().toLowerCase().startsWith("<!doctype html")) {
          message = text;
        }
      } catch {
        // Keep the status-based message if the response body was already consumed.
      }
    }
    throw new Error(message);
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

export function login(username, password) {
  return request("/auth/login/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

export function register(data) {
  return request("/auth/register/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function logout() {
  return request("/auth/logout/", { method: "POST" });
}

export function fetchMe() {
  return request("/auth/me/");
}

export function fetchCourses() {
  return request("/courses/");
}

export function fetchCourse(id) {
  return request(`/courses/${id}/`);
}

export function createCourse(data) {
  const formData = new FormData();
  formData.append("title", data.title);
  formData.append("description", data.description);

  return request("/courses/", {
    method: "POST",
    body: formData,
  });
}

export function uploadCourseOutline(courseId, formData) {
  return request(`/courses/${courseId}/upload-outline/`, {
    method: "POST",
    body: formData,
  });
}

export function confirmCourseOutline(courseId) {
  return request(`/courses/${courseId}/confirm-outline/`, {
    method: "POST",
  });
}

export function createOutlineNode(courseId, data) {
  return request(`/courses/${courseId}/outline-nodes/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function updateOutlineNode(courseId, nodeId, data) {
  return request(`/courses/${courseId}/outline-nodes/${nodeId}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function deleteOutlineNode(courseId, nodeId) {
  return request(`/courses/${courseId}/outline-nodes/${nodeId}/`, {
    method: "DELETE",
  });
}

export function uploadLearningMaterial(courseId, formData) {
  return request(`/courses/${courseId}/upload-material/`, {
    method: "POST",
    body: formData,
  });
}

export function uploadCoursePdf(courseId, formData) {
  return request(`/courses/${courseId}/upload-pdf/`, {
    method: "POST",
    body: formData,
  });
}

export function fetchLearningResources(courseId, nodeId) {
  return request(`/courses/${courseId}/outline-nodes/${nodeId}/learning-resources/`);
}

export function connectLearningObjects(courseId, nodeId, learningObjectIds, label = "") {
  return request(`/courses/${courseId}/outline-nodes/${nodeId}/connect-learning-objects/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      learning_object_ids: learningObjectIds,
      ...(label.trim() ? { label: label.trim() } : {}),
    }),
  });
}

export function separateLearningObject(courseId, nodeId, learningObjectId) {
  return request(`/courses/${courseId}/outline-nodes/${nodeId}/separate-learning-object/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ learning_object_id: learningObjectId }),
  });
}

export function acceptLearningObjectMatchSuggestion(courseId, nodeId, suggestionId) {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/match-suggestions/${suggestionId}/accept/`,
    { method: "POST" },
  );
}

export function rejectLearningObjectMatchSuggestion(courseId, nodeId, suggestionId) {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/match-suggestions/${suggestionId}/reject/`,
    { method: "POST" },
  );
}

export function reviewQuestionPairing(courseId, nodeId, questionId, decision, learningObjectGroupId = null) {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/questions/${questionId}/pairing/`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision,
        ...(learningObjectGroupId !== null
          ? { learning_object_group_id: Number(learningObjectGroupId) }
          : {}),
      }),
    },
  );
}

export function createTopicQuestion(courseId, nodeId, data) {
  return request(`/courses/${courseId}/outline-nodes/${nodeId}/questions/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function deleteLearningMaterial(courseId, materialId) {
  return request(`/courses/${courseId}/materials/${materialId}/`, {
    method: "DELETE",
  });
}

export function createLearningObject(courseId, materialId, data) {
  return request(`/courses/${courseId}/materials/${materialId}/learning-objects/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function updateLearningObject(courseId, materialId, objectId, data) {
  return request(`/courses/${courseId}/materials/${materialId}/learning-objects/${objectId}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function deleteLearningObject(courseId, materialId, objectId) {
  return request(`/courses/${courseId}/materials/${materialId}/learning-objects/${objectId}/`, {
    method: "DELETE",
  });
}

export function confirmLearningObjects(courseId, materialId) {
  return request(`/courses/${courseId}/materials/${materialId}/confirm-learning-objects/`, {
    method: "POST",
  });
}

export function generateAudioPlaylist(courseId, materialId, scope = "all") {
  return request(`/courses/${courseId}/materials/${materialId}/generate-audio-playlist/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope }),
  });
}

export function updateClassifiedBlock(courseId, materialId, blockId, data) {
  return request(`/courses/${courseId}/materials/${materialId}/classified-blocks/${blockId}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function deleteCourseOutline(courseId) {
  return request(`/courses/${courseId}/delete-outline/`, {
    method: "DELETE",
  });
}

export function deleteCourse(id) {
  return request(`/courses/${id}/`, { method: "DELETE" });
}
