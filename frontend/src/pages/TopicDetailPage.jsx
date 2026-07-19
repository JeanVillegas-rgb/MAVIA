import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  confirmLearningObjects,
  createLearningObject,
  deleteLearningObject,
  fetchCourse,
  generateAudioPlaylist,
  regenerateLearningMaterial,
  updateLearningObject,
  uploadLearningMaterial,
} from "../api";

function flattenNodes(nodes = []) {
  return nodes.flatMap((node) => [node, ...flattenNodes(node.children || [])]);
}

function findTopLevelNode(topic, allTopics) {
  if (!topic) return null;
  const topicById = new Map(allTopics.map((node) => [node.id, node]));
  let current = topic;
  while (current?.parent) {
    const parent = topicById.get(current.parent);
    if (!parent) break;
    current = parent;
  }
  return current;
}

const emptyLearningObjectForm = {
  title: "",
  content: "",
};

function LearningObjectForm({ initialValue, submitLabel, busy, onCancel, onSubmit }) {
  const [formData, setFormData] = useState(initialValue || emptyLearningObjectForm);

  function updateField(field, value) {
    setFormData((current) => ({ ...current, [field]: value }));
  }

  function handleSubmit(event) {
    event.preventDefault();
    onSubmit({
      title: formData.title.trim(),
      content: formData.content.trim(),
    });
  }

  return (
    <form
      className="learning-object-form"
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
      onSubmit={handleSubmit}
    >
      <label>
        Title
        <input
          value={formData.title}
          disabled={busy}
          required
          onChange={(event) => updateField("title", event.target.value)}
          placeholder="Learning object title"
        />
      </label>
      <label>
        Content
        <textarea
          value={formData.content}
          disabled={busy}
          required
          rows={5}
          onChange={(event) => updateField("content", event.target.value)}
          placeholder="Teacher-reviewed learning object content"
        />
      </label>
      <div className="learning-object-form-actions">
        <button className="btn btn-secondary btn-small" type="button" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
        <button className="btn btn-primary btn-small" type="submit" disabled={busy}>
          {busy ? "Saving..." : submitLabel}
        </button>
      </div>
    </form>
  );
}

function MaterialCard({ material, courseId, onCourseChange, onError, onMessage }) {
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [reviewEditMode, setReviewEditMode] = useState(false);
  const [selectedId, setSelectedId] = useState(material.learning_objects[0]?.id || null);
  const [busyAction, setBusyAction] = useState("");
  const llmMetadata = material.generated_json?.llm_metadata;
  const generatedJson = material.generated_json || {};
  const learningObjectsConfirmed = Boolean(generatedJson.learning_objects_confirmed);
  const lessonPlaylist = generatedJson.lesson_playlist || [];
  const audioGenerated = Boolean(generatedJson.audio_playlist_generated);
  const canEditLearningObjects = !learningObjectsConfirmed || reviewEditMode;
  const selectedObject = material.learning_objects.find((item) => item.id === selectedId);

  useEffect(() => {
    if (!material.learning_objects.some((item) => item.id === editingId)) {
      setEditingId(null);
    }
    if (!material.learning_objects.length) {
      setSelectedId(null);
      return;
    }
    if (!material.learning_objects.some((item) => item.id === selectedId)) {
      setSelectedId(material.learning_objects[0].id);
    }
  }, [material.learning_objects, editingId, selectedId]);

  useEffect(() => {
    if (learningObjectsConfirmed) {
      setReviewEditMode(false);
      setCreating(false);
      setEditingId(null);
    }
  }, [learningObjectsConfirmed]);

  async function saveNewLearningObject(data) {
    setBusyAction("create");
    onError("");
    onMessage("");
    try {
      const updatedCourse = await createLearningObject(courseId, material.id, data);
      onCourseChange(updatedCourse);
      setCreating(false);
      setReviewEditMode(true);
      const updatedMaterial = updatedCourse.materials?.find((item) => item.id === material.id);
      const createdObject = updatedMaterial?.learning_objects?.[updatedMaterial.learning_objects.length - 1];
      if (createdObject) setSelectedId(createdObject.id);
      onMessage("Learning object added. Review and confirm the final list.");
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  async function saveLearningObject(objectId, data) {
    setBusyAction(`edit-${objectId}`);
    onError("");
    onMessage("");
    try {
      const updatedCourse = await updateLearningObject(courseId, material.id, objectId, data);
      onCourseChange(updatedCourse);
      setEditingId(null);
      setReviewEditMode(true);
      setSelectedId(objectId);
      onMessage("Learning object updated. Confirm again when the list is final.");
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  async function removeLearningObject(objectId) {
    if (!window.confirm("Delete this learning object?")) return;
    setBusyAction(`delete-${objectId}`);
    onError("");
    onMessage("");
    try {
      const updatedCourse = await deleteLearningObject(courseId, material.id, objectId);
      onCourseChange(updatedCourse);
      setReviewEditMode(true);
      const updatedMaterial = updatedCourse.materials?.find((item) => item.id === material.id);
      setSelectedId(updatedMaterial?.learning_objects?.[0]?.id || null);
      onMessage("Learning object deleted. Confirm again when the list is final.");
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  async function confirmObjects() {
    setBusyAction("confirm");
    onError("");
    onMessage("");
    try {
      const updatedCourse = await confirmLearningObjects(courseId, material.id);
      onCourseChange(updatedCourse);
      setReviewEditMode(false);
      onMessage("Learning objects confirmed.");
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  async function generateAudio() {
    setBusyAction("audio");
    onError("");
    onMessage("");
    try {
      const response = await generateAudioPlaylist(courseId, material.id);
      onCourseChange(response.course);
      onMessage(`${response.generated_count} audio playlist item${response.generated_count === 1 ? "" : "s"} generated.`);
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  async function regenerateOutputs() {
    setBusyAction("regenerate");
    onError("");
    onMessage("");
    try {
      const updatedCourse = await regenerateLearningMaterial(courseId, material.id);
      onCourseChange(updatedCourse);
      const updatedMaterial = updatedCourse.materials?.find((item) => item.id === material.id);
      const objectCount = updatedMaterial?.learning_objects?.length || 0;
      onMessage(
        objectCount
          ? `${objectCount} learning object${objectCount === 1 ? "" : "s"} regenerated from the PDF.`
          : "Regeneration finished, but no learning objects were extracted.",
      );
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  return (
    <article className="topic-material-card">
      <div className="material-header">
        <div>
          <h4>{material.title}</h4>
          <p>{material.filename}</p>
          {llmMetadata && (
            <p className="muted-text">
              Provider: Local Ollama · Text model: {llmMetadata.model || "llama3.2:3b"} · Vision model:{" "}
              {llmMetadata.vision_model || "gemma3:4b"}
            </p>
          )}
        </div>
        <div className="generated-item-actions">
          <button
            className="btn btn-secondary btn-small"
            type="button"
            disabled={Boolean(busyAction)}
            onClick={regenerateOutputs}
          >
            {busyAction === "regenerate" ? "Regenerating..." : "Regenerate extraction"}
          </button>
          <span className={`status-pill status-${material.status}`}>{material.status}</span>
        </div>
      </div>

      {material.error_message && <div className="error-banner">{material.error_message}</div>}

      <div className="success-banner">
        Review only the learning objects extracted from this PDF. You can add, edit, delete, then confirm.
      </div>

      <section className="generated-result-panel">
        <div className="generated-section-header">
          <div>
            <h5>Learning Objects</h5>
            <p className="muted-text">This is the teacher-reviewed lesson content that will be used next.</p>
          </div>
          <div className="generated-item-actions">
            <span className={`status-pill ${learningObjectsConfirmed ? "status-completed" : "status-processing"}`}>
              {learningObjectsConfirmed && !reviewEditMode ? "Confirmed" : "Needs review"}
            </span>
            {learningObjectsConfirmed && !reviewEditMode ? (
              <button
                className="btn btn-secondary btn-small"
                type="button"
                disabled={Boolean(busyAction)}
                onClick={() => setReviewEditMode(true)}
              >
                Edit learning objects
              </button>
            ) : (
              <button
                className="btn btn-secondary btn-small"
                type="button"
                disabled={Boolean(busyAction)}
                onClick={() => setCreating(true)}
              >
                Add object
              </button>
            )}
          </div>
        </div>

        {creating && canEditLearningObjects && (
          <LearningObjectForm
            submitLabel="Create object"
            busy={busyAction === "create"}
            onCancel={() => setCreating(false)}
            onSubmit={saveNewLearningObject}
          />
        )}

        {!material.learning_objects.length ? (
          <p className="muted-text">No learning objects generated yet.</p>
        ) : (
          <>
            {canEditLearningObjects && (
              <div className="learning-object-toolbar">
                <div>
                  <span className="object-kind">Selected</span>
                  <strong>{selectedObject?.title || "Choose a learning object"}</strong>
                </div>
                <div className="generated-item-actions">
                  <button
                    className="btn btn-secondary btn-small"
                    type="button"
                    disabled={!selectedObject || Boolean(busyAction)}
                    onClick={() => setEditingId(selectedObject.id)}
                  >
                    Edit selected
                  </button>
                  <button
                    className="btn btn-danger btn-small"
                    type="button"
                    disabled={!selectedObject || Boolean(busyAction)}
                    onClick={() => removeLearningObject(selectedObject.id)}
                  >
                    {selectedObject && busyAction === `delete-${selectedObject.id}` ? "Deleting..." : "Delete selected"}
                  </button>
                </div>
              </div>
            )}

            <div className="generated-list learning-object-list">
              {material.learning_objects.map((item, index) => (
                <div
                  key={item.id}
                  className={`generated-item learning-object-card ${selectedId === item.id && canEditLearningObjects ? "is-selected" : ""} ${canEditLearningObjects ? "" : "is-locked"}`}
                  role={canEditLearningObjects ? "button" : undefined}
                  tabIndex={canEditLearningObjects ? 0 : undefined}
                  onClick={() => {
                    if (canEditLearningObjects && editingId !== item.id) setSelectedId(item.id);
                  }}
                  onKeyDown={(event) => {
                    if (!canEditLearningObjects || editingId === item.id) return;
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelectedId(item.id);
                    }
                  }}
                >
                  {editingId === item.id && canEditLearningObjects ? (
                    <LearningObjectForm
                      initialValue={item}
                      submitLabel="Save changes"
                      busy={busyAction === `edit-${item.id}`}
                      onCancel={() => setEditingId(null)}
                      onSubmit={(data) => saveLearningObject(item.id, data)}
                    />
                  ) : (
                    <>
                      <div className="learning-object-card-header">
                        <div className="learning-object-title">
                          <span>{index + 1}</span>
                          <strong>{item.title}</strong>
                        </div>
                      </div>
                      <p>{item.content}</p>
                    </>
                  )}
                </div>
              ))}
            </div>

            {canEditLearningObjects && (
              <div className="bottom-action-row">
                <button
                  className="btn btn-primary"
                  type="button"
                  disabled={!material.learning_objects.length || Boolean(busyAction)}
                  onClick={confirmObjects}
                >
                  {busyAction === "confirm" ? "Confirming..." : "Confirm learning objects"}
                </button>
              </div>
            )}
          </>
        )}
      </section>

      <section className="generated-result-panel">
        <div className="generated-section-header">
          <div>
            <h5>Lesson Playlist</h5>
            <p className="muted-text">Audio is generated from the confirmed learning objects for this topic.</p>
          </div>
          <button
            className="btn btn-primary btn-small"
            type="button"
            disabled={!material.learning_objects.length || Boolean(busyAction)}
            onClick={generateAudio}
          >
            {busyAction === "audio" ? "Generating audio..." : audioGenerated ? "Regenerate audio" : "Generate audio"}
          </button>
        </div>

        {!lessonPlaylist.length ? (
          <p className="muted-text">No playlist items are ready yet.</p>
        ) : (
          <div className="generated-list">
            {lessonPlaylist.map((item, index) => (
              <div className="generated-item playlist-item" key={`${item.narration_item_order}-${index}`}>
                <span>{index + 1}</span>
                <div>
                  <strong>{item.title || `Playlist item ${index + 1}`}</strong>
                  <small>{item.type || "lesson"}</small>
                </div>
                {item.audio_url ? (
                  <audio controls src={item.audio_url}>
                    <track kind="captions" />
                  </audio>
                ) : (
                  <small className="muted-text">No audio yet</small>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </article>
  );
}

export default function TopicDetailPage() {
  const { courseId, topicId } = useParams();
  const [course, setCourse] = useState(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await fetchCourse(courseId);
        if (!cancelled) {
          setCourse(data);
          setError("");
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [courseId]);

  const allTopics = useMemo(() => flattenNodes(course?.hierarchy || []), [course?.hierarchy]);
  const topic = allTopics.find((node) => String(node.id) === String(topicId));
  const selectedModule = findTopLevelNode(topic, allTopics);
  const materials = (course?.materials || []).filter(
    (material) => String(material.outline_node) === String(topicId),
  );

  async function handleMaterialUpload(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !topic) return;

    setUploading(true);
    setError("");
    setMessage("");
    try {
      const formData = new FormData();
      formData.append("pdf_file", file);
      formData.append("title", file.name.replace(/\.pdf$/i, ""));
      formData.append("outline_node_id", topic.id);
      const updatedCourse = await uploadLearningMaterial(courseId, formData);
      setCourse(updatedCourse);
      const uploadedMaterial = [...(updatedCourse.materials || [])]
        .filter((material) => String(material.outline_node) === String(topic.id))
        .sort((a, b) => Number(b.id) - Number(a.id))[0];
      if (uploadedMaterial?.status === "failed") {
        setError(uploadedMaterial.error_message || "Content generation failed for this PDF.");
        return;
      }
      const objectCount = uploadedMaterial?.learning_objects?.length || 0;
      setMessage(
        objectCount
          ? `${objectCount} learning object${objectCount === 1 ? "" : "s"} extracted from the uploaded PDF.`
          : "No learning objects were extracted. You can add them manually below.",
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  }

  if (error && !course) {
    return (
      <div className="card">
        <Link to={`/courses/${courseId}`} style={{ color: "var(--muted)" }}>
          Back to course
        </Link>
        <div className="error-banner">{error}</div>
      </div>
    );
  }

  if (!course) return <div className="empty-state">Loading topic...</div>;

  if (!topic) {
    return (
      <section className="card">
        <Link to={`/courses/${courseId}`} style={{ color: "var(--muted)" }}>
          Back to course
        </Link>
        <div className="empty-state">Topic not found.</div>
      </section>
    );
  }

  return (
    <section className="card topic-detail-page">
      <Link to={`/courses/${courseId}`} style={{ color: "var(--muted)" }}>
        Back to hierarchy
      </Link>
      <div className="topic-detail-header">
        <div>
          <h2>{topic.title}</h2>
          <p className="muted-text">
            Selected module/topic: {selectedModule?.title || topic.title} / {topic.title}
          </p>
        </div>
        <label className="btn btn-primary">
          {uploading ? "Processing..." : "Upload lesson PDF"}
          <input
            type="file"
            accept=".pdf,application/pdf"
            hidden
            disabled={uploading}
            onChange={handleMaterialUpload}
          />
        </label>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {message && <div className="success-banner">{message}</div>}

      {!materials.length ? (
        <div className="empty-state">No learning material is stored under this topic yet.</div>
      ) : (
        <div className="topic-material-list">
          {materials.map((material) => (
            <MaterialCard
              key={material.id}
              material={material}
              courseId={courseId}
              onCourseChange={setCourse}
              onError={setError}
              onMessage={setMessage}
            />
          ))}
        </div>
      )}
    </section>
  );
}
