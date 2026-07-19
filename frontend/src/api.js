const API_BASE = "/api";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
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

export function generateAudioPlaylist(courseId, materialId) {
  return request(`/courses/${courseId}/materials/${materialId}/generate-audio-playlist/`, {
    method: "POST",
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

export function fetchModuleConceptDag(courseId, moduleId) {
  return request(`/courses/${courseId}/modules/${moduleId}/learning-object-dag/`);
}

export function fetchModuleConcepts(courseId, moduleId) {
  return request(`/courses/${courseId}/modules/${moduleId}/concepts/`);
}

export function extractModuleConcepts(courseId, moduleId) {
  return request(`/courses/${courseId}/modules/${moduleId}/extract-concepts/`, {
    method: "POST",
  });
}

export function approveModuleConcepts(courseId, moduleId) {
  return request(`/courses/${courseId}/modules/${moduleId}/approve-concepts/`, {
    method: "POST",
  });
}

export function generateModuleConceptDag(courseId, moduleId) {
  return request(`/courses/${courseId}/modules/${moduleId}/generate-learning-object-dag/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ regenerate: true }),
  });
}

export function updateModuleConceptEdge(courseId, moduleId, edgeId, data) {
  return request(`/courses/${courseId}/modules/${moduleId}/learning-object-edges/${edgeId}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function createModuleConceptEdge(courseId, moduleId, data) {
  return request(`/courses/${courseId}/modules/${moduleId}/learning-object-edges/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function deleteModuleConceptEdge(courseId, moduleId, edgeId) {
  return request(`/courses/${courseId}/modules/${moduleId}/learning-object-edges/${edgeId}/`, {
    method: "DELETE",
  });
}

export function confirmModuleConceptDag(courseId, moduleId) {
  return request(`/courses/${courseId}/modules/${moduleId}/confirm-learning-object-dag/`, {
    method: "POST",
  });
}

export function startQuestionGeneration(materialId, nodeId = null) {
  return request(`/generation/materials/${materialId}/start/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(nodeId ? { node_id: nodeId } : {}),
  });
}

export function fetchQuestionRuns(materialId) {
  return request(`/generation/runs/?material_id=${materialId}`);
}

export function fetchQuestionRunEvents(runId, afterSeq = 0) {
  return request(`/generation/runs/${runId}/events/?after=${afterSeq}`);
}

export function fetchMaterialQuestions(materialId) {
  return request(`/generation/materials/${materialId}/questions/`);
}

export function updateGeneratedQuestion(questionId, data) {
  return request(`/generation/questions/${questionId}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function deleteGeneratedQuestion(questionId) {
  return request(`/generation/questions/${questionId}/`, {
    method: "DELETE",
  });
}
