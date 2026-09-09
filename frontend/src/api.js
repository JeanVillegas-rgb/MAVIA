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
      "API server unreachable. Start the Django backend at http://127.0.0.1:8000, then try again."
    );
  }

  if (!response.ok) {
    let message = `Request failed: ${response.status}`;
    const responseForText = response.clone();
    try {
      const data = await response.json();
      if (data.detail) {
        message = data.detail;
      } else if (Array.isArray(data.non_field_errors)) {
        message = data.non_field_errors[0];
      } else if (data && typeof data === "object") {
        const first = Object.values(data)[0];
        message = Array.isArray(first) ? first[0] : String(first);
      }
    } catch {
      try {
        const text = await responseForText.text();
        if (text && !text.trim().toLowerCase().startsWith("<!doctype html")) {
          message = text;
        }
      } catch {
        /* keep status-based message */
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

export function verifyEmail(token) {
  return request(`/auth/verify-email/${encodeURIComponent(token)}/`);
}

export function resendVerification(email) {
  return request("/auth/resend-verification/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
}

export function logout() {
  return request("/auth/logout/", { method: "POST" });
}

export function fetchMe() {
  return request("/auth/me/");
}

export function fetchAdaptiveConfig() {
  return request("/adaptive-config/");
}

export function updateAdaptiveConfig(data) {
  return request("/adaptive-config/", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function resetAdaptiveConfig() {
  return request("/adaptive-config/reset/", { method: "POST" });
}

// ---------------------------------------------------------------------------
// lessons / content-generation pipeline  (ported from origin/Milestone1-Jure)
// ---------------------------------------------------------------------------

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
  return request("/courses/", { method: "POST", body: formData });
}

export function deleteCourse(id) {
  return request(`/courses/${id}/`, { method: "DELETE" });
}

export function uploadCourseOutline(courseId, formData) {
  return request(`/courses/${courseId}/upload-outline/`, {
    method: "POST",
    body: formData,
  });
}

export function confirmCourseOutline(courseId) {
  return request(`/courses/${courseId}/confirm-outline/`, { method: "POST" });
}

export function deleteCourseOutline(courseId) {
  return request(`/courses/${courseId}/delete-outline/`, { method: "DELETE" });
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
  return request(
    `/courses/${courseId}/materials/${materialId}/learning-objects/${objectId}/`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }
  );
}

export function deleteLearningObject(courseId, materialId, objectId) {
  return request(
    `/courses/${courseId}/materials/${materialId}/learning-objects/${objectId}/`,
    { method: "DELETE" }
  );
}

export function confirmLearningObjects(courseId, materialId) {
  return request(
    `/courses/${courseId}/materials/${materialId}/confirm-learning-objects/`,
    { method: "POST" }
  );
}

export function regenerateImageNarrations(courseId, materialId) {
  return request(
    `/courses/${courseId}/materials/${materialId}/regenerate-image-narrations/`,
    { method: "POST" }
  );
}

export function generateAudioPlaylist(courseId, materialId, scope = "all") {
  return request(
    `/courses/${courseId}/materials/${materialId}/generate-audio-playlist/`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope }),
    }
  );
}

export function updateClassifiedBlock(courseId, materialId, blockId, data) {
  return request(
    `/courses/${courseId}/materials/${materialId}/classified-blocks/${blockId}/`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }
  );
}

export function fetchLearningResources(courseId, nodeId) {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/learning-resources/`
  );
}

export function connectLearningObjects(courseId, nodeId, learningObjectIds, label = "") {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/connect-learning-objects/`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        learning_object_ids: learningObjectIds,
        ...(label.trim() ? { label: label.trim() } : {}),
      }),
    }
  );
}

export function separateLearningObject(courseId, nodeId, learningObjectId) {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/separate-learning-object/`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ learning_object_id: learningObjectId }),
    }
  );
}

export function acceptLearningObjectMatchSuggestion(courseId, nodeId, suggestionId) {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/match-suggestions/${suggestionId}/accept/`,
    { method: "POST" }
  );
}

export function rejectLearningObjectMatchSuggestion(courseId, nodeId, suggestionId) {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/match-suggestions/${suggestionId}/reject/`,
    { method: "POST" }
  );
}

export function reviewQuestionPairing(
  courseId,
  nodeId,
  questionId,
  decision,
  learningObjectGroupId = null
) {
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
    }
  );
}

export function createTopicQuestion(courseId, nodeId, data) {
  return request(`/courses/${courseId}/outline-nodes/${nodeId}/questions/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function deleteTopicQuestion(courseId, nodeId, questionId) {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/questions/${questionId}/`,
    { method: "DELETE" }
  );
}

export function updateTopicQuestion(courseId, nodeId, questionId, data) {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/questions/${questionId}/`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }
  );
}

export function startQuestionGeneration(materialId) {
  return request(`/generation/materials/${materialId}/start/`, { method: "POST" });
}

export function fetchQuestionGenerationTrace(runId) {
  return request(`/generation/runs/${runId}/events/`);
}

export function deleteTopicLearningObject(courseId, nodeId, objectId) {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/learning-objects/${objectId}/`,
    { method: "DELETE" }
  );
}

export function publishTopic(courseId, nodeId) {
  return request(`/courses/${courseId}/outline-nodes/${nodeId}/publish/`, {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Course review section (teacher): inspect the packaged lesson content + see
// each enrolled student's progress. Playback is mobile-only.
// ---------------------------------------------------------------------------

export function fetchReviewModules(courseId) {
  return request(`/courses/${courseId}/review/modules/`);
}

export function fetchModulePackage(courseId, moduleId) {
  return request(`/courses/${courseId}/review/modules/${moduleId}/package/`);
}

export function fetchCourseProgress(courseId) {
  return request(`/adaptive-portal/courses/${courseId}/progress/`);
}

export function fetchEnrollments(courseId) {
  return request(`/adaptive-portal/courses/${courseId}/enrollments/`);
}

export function addEnrollment(courseId, studentId) {
  return request(`/adaptive-portal/courses/${courseId}/enrollments/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ student_id: studentId }),
  });
}

export function removeEnrollment(courseId, enrollmentId) {
  return request(`/adaptive-portal/courses/${courseId}/enrollments/${enrollmentId}/`, {
    method: "DELETE",
  });
}

export function searchStudents(query = "") {
  const q = query.trim() ? `?q=${encodeURIComponent(query.trim())}` : "";
  return request(`/adaptive-portal/students/${q}`);
}

export function assignVersionSlot(courseId, nodeId, learningObjectId, slot) {
  return request(`/courses/${courseId}/outline-nodes/${nodeId}/version-assignment/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ learning_object_id: learningObjectId, slot }),
  });
}

// One object at a time: a single Gemma call runs for minutes, so this request
// is deliberately slow and the caller must show that it is working.
export function generateObjectVersions(courseId, nodeId, learningObjectId) {
  return request(
    `/courses/${courseId}/outline-nodes/${nodeId}/learning-objects/${learningObjectId}/generate-versions/`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
  );
}

export function editVersionText(courseId, nodeId, variantId, narration) {
  return request(`/courses/${courseId}/outline-nodes/${nodeId}/versions/${variantId}/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ narration }),
  });
}

// Publishing runs in the background; this is how its progress is followed.
// Pass the highest seq already seen so each poll returns only what is new.
export function fetchGenerationRunEvents(runId, after = 0) {
  return request(`/generation/runs/${runId}/events/?after=${after}`);
}
