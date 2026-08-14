import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  confirmLearningObjects,
  createLearningObject,
  deleteLearningMaterial,
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

function isImageLearningObject(item) {
  return item?.kind === "image" || Boolean(item?.image_url);
}

function LearningObjectForm({ initialValue = null, submitLabel, busy, onCancel, onSubmit }) {
  const isImage = isImageLearningObject(initialValue);
  const [form, setForm] = useState({
    title: initialValue?.title || "",
    content: initialValue?.content || "",
    image_url: initialValue?.image_url || "",
  });

  function updateField(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function submitForm(event) {
    event.preventDefault();
    onSubmit({
      title: form.title.trim(),
      content: form.content.trim(),
      image_url: form.image_url.trim(),
    });
  }

  return (
    <form
      className={`learning-object-form ${isImage ? "image-learning-object-form" : ""}`}
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
      onSubmit={submitForm}
    >
      {isImage && form.image_url && (
        <figure className="learning-object-image-preview">
          <img src={form.image_url} alt={form.title || "Extracted learning object"} />
        </figure>
      )}
      {isImage && (
        <div className="image-description-notice">
          <span className="image-kind-icon" aria-hidden="true" />
          <span>Teacher image description required.</span>
        </div>
      )}
      <label>
        Title
        <input
          value={form.title}
          disabled={busy}
          required
          onChange={(event) => updateField("title", event.target.value)}
        />
      </label>
      {isImage && (
        <label>
          Image URL
          <input
            value={form.image_url}
            disabled={busy}
            onChange={(event) => updateField("image_url", event.target.value)}
          />
        </label>
      )}
      <label>
        {isImage ? "Teacher image description" : "Content"}
        <textarea
          value={form.content}
          disabled={busy}
          required
          rows={isImage ? 4 : 5}
          placeholder={isImage ? "Describe the image for the learner." : ""}
          onChange={(event) => updateField("content", event.target.value)}
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
  const [activeTab, setActiveTab] = useState("content");
  const [showAudioWarning, setShowAudioWarning] = useState(false);

  const generatedJson = material.generated_json || {};
  const learningObjectsConfirmed = Boolean(generatedJson.learning_objects_confirmed);
  const lessonPlaylist = generatedJson.lesson_playlist || [];
  const lessonAudioGenerated = Boolean(generatedJson.lesson_audio_generated);
  const audioCount = lessonPlaylist.filter((item) => item.audio_url).length;
  const canEditLearningObjects = !learningObjectsConfirmed || reviewEditMode;
  const selectedObject = material.learning_objects.find((item) => item.id === selectedId);
  const imagesMissingDescription = material.learning_objects.filter(
    (item) => isImageLearningObject(item) && !item.content?.trim()
  );

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
      onMessage("Learning objects confirmed and saved to the database.");
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  async function regenerateMaterial() {
    if (!window.confirm("Regenerate extracted learning objects from the PDF? This replaces the current list.")) return;
    setBusyAction("regenerate");
    onError("");
    onMessage("");
    try {
      const updatedCourse = await regenerateLearningMaterial(courseId, material.id);
      onCourseChange(updatedCourse);
      const updatedMaterial = updatedCourse.materials?.find((item) => item.id === material.id);
      const objectCount = updatedMaterial?.learning_objects?.length || 0;
      setSelectedId(updatedMaterial?.learning_objects?.[0]?.id || null);
      setReviewEditMode(true);
      onMessage(`${objectCount} learning object${objectCount === 1 ? "" : "s"} extracted from the PDF.`);
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  async function removeMaterial() {
    if (!window.confirm("Delete this uploaded lesson PDF and its extracted content?")) return;
    setBusyAction("delete-material");
    onError("");
    onMessage("");
    try {
      const updatedCourse = await deleteLearningMaterial(courseId, material.id);
      onCourseChange(updatedCourse);
      onMessage("Lesson material deleted.");
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  async function generateAudio() {
    setShowAudioWarning(false);
    setBusyAction("audio-lessons");
    onError("");
    onMessage("");
    try {
      const response = await generateAudioPlaylist(courseId, material.id, "lessons");
      onCourseChange(response.course);
      onMessage(`${response.generated_count} lesson audio track${response.generated_count === 1 ? "" : "s"} generated.`);
      setActiveTab("audio");
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  function requestAudioGeneration() {
    if (imagesMissingDescription.length) {
      setShowAudioWarning(true);
      return;
    }
    generateAudio();
  }

  function editMissingImageDescription() {
    const imageObject = imagesMissingDescription[0];
    if (!imageObject) return;
    setShowAudioWarning(false);
    setActiveTab("content");
    setReviewEditMode(true);
    setSelectedId(imageObject.id);
    setEditingId(imageObject.id);
  }

  return (
    <article className="material-card">
      <div className="material-header">
        <div>
          <h4>{material.title}</h4>
          <p>
            {material.filename} · {material.learning_objects.length} learning object
            {material.learning_objects.length === 1 ? "" : "s"}
          </p>
        </div>
        <span className={`status-pill status-${material.status}`}>{material.status}</span>
      </div>

      {material.status === "failed" && (
        <div className="error-banner">{material.error_message || "Content extraction failed."}</div>
      )}

      <div className="generated-item-actions" style={{ marginTop: "0.75rem" }}>
        <button
          className="btn btn-secondary btn-small"
          type="button"
          disabled={Boolean(busyAction)}
          onClick={regenerateMaterial}
        >
          {busyAction === "regenerate" ? "Regenerating..." : "Regenerate extraction"}
        </button>
        <button
          className="btn btn-danger btn-small"
          type="button"
          disabled={Boolean(busyAction)}
          onClick={removeMaterial}
        >
          {busyAction === "delete-material" ? "Deleting..." : "Delete material"}
        </button>
      </div>

      <div className="material-workspace-tabs">
        <button
          type="button"
          className={activeTab === "content" ? "is-active" : ""}
          onClick={() => setActiveTab("content")}
        >
          Content <span>{material.learning_objects.length}</span>
        </button>
        <button
          type="button"
          className={activeTab === "audio" ? "is-active" : ""}
          onClick={() => setActiveTab("audio")}
        >
          Audio <span>{audioCount}</span>
        </button>
      </div>

      {activeTab === "content" && (
        <section className="generated-result-panel">
          <div className="generated-section-header">
            <div>
              <h5>Learning Objects</h5>
              <p className="muted-text">Extracted PDF content and teacher-provided image descriptions saved in the database.</p>
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

          {imagesMissingDescription.length > 0 && (
            <div className="missing-description-summary" role="alert">
              <span className="warning-mark" aria-hidden="true">!</span>
              <div>
                <strong>
                  {imagesMissingDescription.length} image learning object
                  {imagesMissingDescription.length === 1 ? " has" : "s have"} no description
                </strong>
                <p>Add a teacher description before generating complete lesson audio.</p>
              </div>
            </div>
          )}

          {!material.learning_objects.length ? (
            <p className="muted-text">No learning objects extracted yet.</p>
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
                {material.learning_objects.map((item, index) => {
                  const isImage = isImageLearningObject(item);
                  const isMissingImageDescription = isImage && !item.content?.trim();
                  const sectionTitle = item.section_title?.trim() || "";
                  const previousSectionTitle = material.learning_objects[index - 1]?.section_title?.trim() || "";
                  const startsSection = Boolean(sectionTitle) && sectionTitle !== previousSectionTitle;
                  const isSectionParent = Boolean(sectionTitle) && item.title.trim().toLocaleLowerCase() === sectionTitle.toLocaleLowerCase();
                  return (
                    <Fragment key={item.id}>
                      {startsSection && !isSectionParent && (
                        <div className="learning-object-section-heading">
                          <h6>{sectionTitle}</h6>
                        </div>
                      )}
                      <div
                        className={`generated-item learning-object-card ${sectionTitle && !isSectionParent ? "is-section-child" : ""} ${isImage ? "is-image-object" : ""} ${selectedId === item.id && canEditLearningObjects ? "is-selected" : ""} ${canEditLearningObjects ? "" : "is-locked"}`}
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
                              {isImage && (
                                <span
                                  className={`image-object-badge ${isMissingImageDescription ? "is-missing" : ""}`}
                                  title={isMissingImageDescription ? "Image description missing" : "Image content"}
                                >
                                  {isMissingImageDescription ? (
                                    <span className="warning-mark warning-mark-small" aria-hidden="true">!</span>
                                  ) : (
                                    <span className="image-kind-icon" aria-hidden="true" />
                                  )}
                                  {isMissingImageDescription ? "Description missing" : "Image"}
                                </span>
                              )}
                            </div>
                            {isImage && item.image_url && (
                              <figure className="learning-object-image-preview">
                                <img src={item.image_url} alt={item.title || `Learning object ${index + 1}`} />
                              </figure>
                            )}
                            {isImage && (
                              <div className={`image-description-notice ${isMissingImageDescription ? "is-missing" : ""}`}>
                                {isMissingImageDescription ? (
                                  <span className="warning-mark warning-mark-small" aria-hidden="true">!</span>
                                ) : (
                                  <span className="image-kind-icon" aria-hidden="true" />
                                )}
                                <span>
                                  {isMissingImageDescription
                                    ? `Learning object ${index + 1} has no image description. Edit this object to add one.`
                                    : "Teacher image description included."}
                                </span>
                              </div>
                            )}
                            <p className={isImage ? "image-description-text" : ""}>{item.content}</p>
                          </>
                        )}
                      </div>
                    </Fragment>
                  );
                })}
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
      )}

      {activeTab === "audio" && (
        <section className="generated-result-panel">
          <div className="generated-section-header">
            <div>
              <h5>Lesson Playlist</h5>
              <p className="muted-text">Audio is generated from confirmed learning objects only.</p>
            </div>
            <div className="generated-item-actions">
              <button
                className="btn btn-primary btn-small"
                type="button"
                disabled={!material.learning_objects.length || Boolean(busyAction)}
                onClick={requestAudioGeneration}
              >
                {busyAction === "audio-lessons"
                  ? "Generating lessons..."
                  : lessonAudioGenerated
                    ? "Regenerate lesson audio"
                    : "Generate lesson audio"}
              </button>
            </div>
          </div>

          {!lessonPlaylist.length ? (
            <p className="muted-text">No playlist items are ready yet.</p>
          ) : (
            <div className="audio-playlist-groups">
              <div className="audio-playlist-group">
                <div className="audio-playlist-group-header">
                  <strong>Lesson narration</strong>
                  <span>{lessonPlaylist.filter((item) => item.audio_url).length} ready</span>
                </div>
                <div className="generated-list">
                  {lessonPlaylist.map((item, index) => (
                    <div className="generated-item playlist-item" key={`${item.narration_item_order || "lesson"}-${index}`}>
                      <span>{index + 1}</span>
                      <div>
                        <strong>{item.title || `Lesson audio ${index + 1}`}</strong>
                        <small>{(item.type || "lesson").replace(/_/g, " ")}</small>
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
              </div>
            </div>
          )}
        </section>
      )}

      {showAudioWarning && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal-card audio-warning-modal" role="dialog" aria-modal="true" aria-labelledby={`audio-warning-${material.id}`}>
            <div className="modal-warning-heading">
              <span className="warning-mark" aria-hidden="true">!</span>
              <h3 id={`audio-warning-${material.id}`}>Image description missing</h3>
            </div>
            <p>
              {imagesMissingDescription.length === 1
                ? `"${imagesMissingDescription[0].title}" has no teacher-provided image description.`
                : `${imagesMissingDescription.length} image learning objects have no teacher-provided descriptions.`}
            </p>
            <p>You can add the description now or continue and generate audio only for items that have narration text.</p>
            <div className="modal-actions">
              <button type="button" className="btn btn-secondary" onClick={() => setShowAudioWarning(false)}>
                Cancel
              </button>
              <button type="button" className="btn btn-secondary" onClick={editMissingImageDescription}>
                Add description
              </button>
              <button type="button" className="btn btn-primary" onClick={generateAudio}>
                Continue without it
              </button>
            </div>
          </div>
        </div>
      )}
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
        setError(uploadedMaterial.error_message || "Content extraction failed for this PDF.");
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
