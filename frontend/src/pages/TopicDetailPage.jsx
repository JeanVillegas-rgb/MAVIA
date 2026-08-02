import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  confirmLearningObjects,
  createLearningObject,
  deleteGeneratedQuestion,
  deleteLearningMaterial,
  deleteLearningObject,
  fetchCourse,
  fetchMaterialQuestions,
  fetchQuestionRunEvents,
  fetchQuestionRuns,
  generateAudioPlaylist,
  regenerateLearningMaterial,
  startQuestionGeneration,
  updateGeneratedQuestion,
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

function QuestionCard({ question, onSaved, onError }) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState(null);

  function startEdit(event) {
    event.stopPropagation();
    setForm({
      question_text: question.question_text,
      choices: question.choices ? { ...question.choices } : null,
      correct_answer: question.correct_answer,
      explanation: question.explanation || "",
    });
    setEditing(true);
  }

  async function saveEdit(event) {
    event.preventDefault();
    setBusy(true);
    onError("");
    try {
      await updateGeneratedQuestion(question.id, form);
      setEditing(false);
      await onSaved();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function removeQuestion(event) {
    event.stopPropagation();
    if (!window.confirm("Delete this question (and any learner answers to it)?")) return;
    setBusy(true);
    onError("");
    try {
      await deleteGeneratedQuestion(question.id);
      await onSaved();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (editing) {
    return (
      <form
        className="learning-object-form question-edit-form"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.stopPropagation()}
        onSubmit={saveEdit}
      >
        <label>
          Question
          <textarea
            value={form.question_text}
            disabled={busy}
            required
            rows={2}
            onChange={(event) => setForm((f) => ({ ...f, question_text: event.target.value }))}
          />
        </label>
        {question.question_format === "MCQ" && form.choices ? (
          <div className="question-edit-choices">
            {Object.entries(form.choices).map(([letter, text]) => (
              <label className="question-edit-choice" key={letter}>
                <input
                  type="radio"
                  name={`correct-${question.id}`}
                  checked={form.correct_answer === letter}
                  disabled={busy}
                  onChange={() => setForm((f) => ({ ...f, correct_answer: letter }))}
                  title="Mark as correct answer"
                />
                <strong>{letter}.</strong>
                <input
                  value={text}
                  disabled={busy}
                  required
                  onChange={(event) =>
                    setForm((f) => ({
                      ...f,
                      choices: { ...f.choices, [letter]: event.target.value },
                    }))
                  }
                />
              </label>
            ))}
            <p className="muted-text">Select the radio button of the correct answer.</p>
          </div>
        ) : (
          <label>
            Correct answer
            <select
              value={form.correct_answer}
              disabled={busy}
              onChange={(event) => setForm((f) => ({ ...f, correct_answer: event.target.value }))}
            >
              <option value="True">True</option>
              <option value="False">False</option>
            </select>
          </label>
        )}
        <label>
          Explanation
          <textarea
            value={form.explanation}
            disabled={busy}
            rows={2}
            onChange={(event) => setForm((f) => ({ ...f, explanation: event.target.value }))}
          />
        </label>
        <div className="learning-object-form-actions">
          <button
            className="btn btn-secondary btn-small"
            type="button"
            disabled={busy}
            onClick={() => setEditing(false)}
          >
            Cancel
          </button>
          <button className="btn btn-primary btn-small" type="submit" disabled={busy}>
            {busy ? "Saving..." : "Save question"}
          </button>
        </div>
      </form>
    );
  }

  return (
    <div className="generated-item question-card">
      <div className="question-card-header">
        <span className={`difficulty-pill difficulty-${question.difficulty}`}>
          {question.difficulty}
        </span>
        <span className="muted-text">
          {question.question_format} &middot; {question.bloom_level}
          {question.category && <> &middot; {question.category}</>}
        </span>
        <span className="question-card-actions">
          <button
            className="btn btn-secondary btn-small"
            type="button"
            disabled={busy}
            onClick={startEdit}
          >
            Edit
          </button>
          <button
            className="btn btn-danger btn-small"
            type="button"
            disabled={busy}
            onClick={removeQuestion}
          >
            {busy ? "..." : "Delete"}
          </button>
        </span>
      </div>
      <p className="question-text">{question.question_text}</p>
      {question.question_format === "MCQ" && question.choices ? (
        <ul className="question-choices">
          {Object.entries(question.choices).map(([letter, text]) => (
            <li
              key={letter}
              className={letter === question.correct_answer ? "is-correct" : ""}
            >
              <strong>{letter}.</strong> {text}
              {letter === question.correct_answer && " ✓"}
            </li>
          ))}
        </ul>
      ) : (
        <p className="question-choices">
          Answer: <strong>{question.correct_answer}</strong>
        </p>
      )}
      {question.explanation && <p className="muted-text">{question.explanation}</p>}
    </div>
  );
}

function QuestionBankSection({
  confirmed,
  running,
  starting,
  totalQuestions,
  progress,
  runFailed,
  log,
  onStart,
}) {
  const logEndRef = useRef(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ block: "nearest" });
  }, [log.length]);

  return (
    <section className="generated-result-panel">
      <div className="generated-section-header">
        <div>
          <h5>Practice Questions</h5>
          <p className="muted-text">
            Generated from each confirmed learning object and shown under its content above.
            Difficulty is assigned by the Bloom&apos;s classifier.
          </p>
        </div>
        <button
          className="btn btn-primary btn-small"
          type="button"
          disabled={!confirmed || running || starting}
          onClick={() => onStart(null)}
        >
          {running || starting
            ? progress?.scope?.startsWith("Only ")
              ? "Please wait"
              : "Generating..."
            : totalQuestions
              ? "Regenerate all questions"
              : "Generate all questions"}
        </button>
      </div>

      {!confirmed && (
        <p className="muted-text">Confirm the learning objects above before generating questions.</p>
      )}

      {progress && (
        <div className="success-banner">
          {progress.scope ? `${progress.scope}: ` : ""}
          Generating&hellip; {progress.generated} question{progress.generated === 1 ? "" : "s"} so far
          &middot; {progress.message}
        </div>
      )}

      {runFailed && (
        <div className="error-banner">Question generation failed. Check the log below and try again.</div>
      )}

      {log.length > 0 && (
        <details className="generation-log" open>
          <summary>
            Generation log <span className="muted-text">&middot; {log.length} events</span>
          </summary>
          <div className="generation-log-lines">
            {log.map((event) => (
              <div className={`generation-log-line log-${event.event_type}`} key={event.seq}>
                <span className="log-time">{new Date(event.created_at).toLocaleTimeString()}</span>
                <span className="log-type">{event.event_type}</span>
                <span className="log-message">{event.message}</span>
              </div>
            ))}
            <div ref={logEndRef} />
          </div>
        </details>
      )}

      {!totalQuestions && !running && !log.length && (
        <p className="muted-text">No questions generated yet.</p>
      )}
    </section>
  );
}

function MaterialCard({ material, courseId, onCourseChange, onError, onMessage }) {
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [reviewEditMode, setReviewEditMode] = useState(false);
  const [selectedId, setSelectedId] = useState(material.learning_objects[0]?.id || null);
  const [busyAction, setBusyAction] = useState("");
  const [activeTab, setActiveTab] = useState("content");
  const [questionBank, setQuestionBank] = useState([]);

  const loadQuestionBank = useCallback(async () => {
    try {
      setQuestionBank(await fetchMaterialQuestions(material.id));
    } catch {
      // backend unreachable — leave the bank as-is
    }
  }, [material.id]);

  useEffect(() => {
    loadQuestionBank();
  }, [loadQuestionBank]);

  const questionsByNode = useMemo(
    () => new Map(questionBank.map((node) => [node.node_id, node.questions])),
    [questionBank],
  );
  const totalQuestions = questionBank.reduce((sum, node) => sum + node.questions.length, 0);

  const [genRun, setGenRun] = useState(null);
  const [genProgress, setGenProgress] = useState(null);
  const [genLog, setGenLog] = useState([]);
  const [genStarting, setGenStarting] = useState(false);
  const [genStartingNodeId, setGenStartingNodeId] = useState(null);
  const genRunning = genRun?.status === "running";
  const allQuestionsRunning = genRunning && !genRun?.node_id;

  useEffect(() => {
    // pick up a run that is already in flight (e.g. after a page reload)
    fetchQuestionRuns(material.id)
      .then((runs) => {
        if (runs[0]?.status === "running") setGenRun(runs[0]);
      })
      .catch(() => {});
  }, [material.id]);

  useEffect(() => {
    if (!genRunning) return undefined;
    let lastSeq = 0;
    let generated = 0;
    let stopped = false;
    let timer = null;

    const poll = async () => {
      try {
        const data = await fetchQuestionRunEvents(genRun.id, lastSeq);
        if (stopped) return;
        if (data.events.length) {
          lastSeq = data.events[data.events.length - 1].seq;
          generated += data.events.filter((e) => e.event_type === "question_generated").length;
          setGenProgress((current) => ({
            generated,
            message: data.events[data.events.length - 1].message,
            scope: current?.scope,
          }));
          setGenLog((current) => [...current, ...data.events]);
          // questions are saved per node — refresh the inline lists as nodes finish
          if (data.events.some((e) => e.event_type === "node_finished")) {
            loadQuestionBank();
          }
        }
        if (data.run.status === "running") {
          timer = setTimeout(poll, 2500);
        } else {
          setGenRun(data.run);
          setGenProgress(null);
          loadQuestionBank();
        }
      } catch {
        if (!stopped) timer = setTimeout(poll, 5000);
      }
    };
    poll();

    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [genRun?.id, genRunning, loadQuestionBank]);

  async function startGeneration(nodeId = null) {
    const targetObject = nodeId
      ? material.learning_objects.find((item) => item.id === nodeId)
      : null;
    const existingCount = nodeId
      ? (questionsByNode.get(nodeId) || []).length
      : totalQuestions;
    if (
      existingCount > 0 &&
      !window.confirm(
        nodeId
          ? "Regenerating replaces this learning object's existing questions (and any learner answers to them). Continue?"
          : "Regenerating replaces this material's existing questions (and any learner answers to them). Continue?",
      )
    ) {
      return;
    }
    setGenStarting(true);
    setGenStartingNodeId(nodeId);
    onError("");
    try {
      setActiveTab("questions");
      const response = await startQuestionGeneration(material.id, nodeId);
      setGenLog([]);
      setGenRun({ id: response.run_id, node_id: response.node_id, status: "running" });
      setGenProgress({
        generated: 0,
        message: "Starting...",
        scope: targetObject ? `Only ${targetObject.title}` : "All learning objects",
      });
    } catch (err) {
      onError(err.message);
    } finally {
      setGenStarting(false);
      setGenStartingNodeId(null);
    }
  }
  const llmMetadata = material.generated_json?.llm_metadata;
  const generatedJson = material.generated_json || {};
  const learningObjectsConfirmed = Boolean(generatedJson.learning_objects_confirmed);
  const lessonPlaylist = generatedJson.lesson_playlist || [];
  const audioGenerated = Boolean(generatedJson.audio_playlist_generated);
  const lessonAudioGenerated = Boolean(generatedJson.lesson_audio_generated);
  const questionAudioGenerated = Boolean(generatedJson.question_audio_generated);
  const audioCount = lessonPlaylist.filter((item) => item.audio_url).length;
  const lessonAudioItems = lessonPlaylist.filter((item) => item.type !== "practice_question");
  const questionAudioItems = lessonPlaylist.filter((item) => item.type === "practice_question");
  const questionAudioCount = questionAudioItems.filter((item) => item.audio_url).length;
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

  async function generateAudio(scope) {
    setBusyAction(`audio-${scope}`);
    onError("");
    onMessage("");
    try {
      setActiveTab("audio");
      const response = await generateAudioPlaylist(courseId, material.id, scope);
      onCourseChange(response.course);
      onMessage(
        `${response.generated_count} ${scope === "questions" ? "question" : "lesson"} audio item${
          response.generated_count === 1 ? "" : "s"
        } generated.`,
      );
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

  async function removeMaterial() {
    if (!window.confirm("Delete this uploaded PDF and its learning objects?")) return;
    setBusyAction("delete-material");
    onError("");
    onMessage("");
    try {
      const updatedCourse = await deleteLearningMaterial(courseId, material.id);
      onCourseChange(updatedCourse);
      onMessage("Uploaded PDF deleted.");
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
          <button
            className="btn btn-danger btn-small"
            type="button"
            disabled={Boolean(busyAction)}
            onClick={removeMaterial}
          >
            {busyAction === "delete-material" ? "Deleting..." : "Delete PDF"}
          </button>
          <span className={`status-pill status-${material.status}`}>{material.status}</span>
        </div>
      </div>

      {material.error_message && <div className="error-banner">{material.error_message}</div>}

      <div className="success-banner">
        Review only the learning objects extracted from this PDF. You can add, edit, delete, then confirm.
      </div>

      <div className="material-workspace-tabs" role="tablist" aria-label="Generated content sections">
        <button
          type="button"
          className={activeTab === "content" ? "is-active" : ""}
          onClick={() => setActiveTab("content")}
        >
          Content <span>{material.learning_objects.length}</span>
        </button>
        <button
          type="button"
          className={activeTab === "questions" ? "is-active" : ""}
          onClick={() => setActiveTab("questions")}
        >
          Questions <span>{totalQuestions}</span>
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
                      {learningObjectsConfirmed && (
                        <div className="generated-item-actions">
                          {(questionsByNode.get(item.id) || []).length > 0 && (
                            <span className="object-kind">
                              {(questionsByNode.get(item.id) || []).length} question
                              {(questionsByNode.get(item.id) || []).length === 1 ? "" : "s"}
                            </span>
                          )}
                          <button
                            className="btn btn-secondary btn-small"
                            type="button"
                            disabled={genRunning || genStarting}
                            onClick={(event) => {
                              event.stopPropagation();
                              startGeneration(item.id);
                            }}
                          >
                            {genStartingNodeId === item.id || (genRunning && genRun?.node_id === item.id)
                              ? "Generating..."
                              : genRunning || genStarting
                                ? "Please wait"
                              : (questionsByNode.get(item.id) || []).length
                                ? "Regenerate questions"
                                : "Generate questions"}
                          </button>
                        </div>
                      )}
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
      )}

      {activeTab === "questions" && (
      <>
      <QuestionBankSection
        confirmed={learningObjectsConfirmed}
        running={genRunning}
        starting={genStarting}
        totalQuestions={totalQuestions}
        progress={genProgress}
        runFailed={genRun?.status === "failed"}
        log={genLog}
        onStart={startGeneration}
      />

      {totalQuestions > 0 && (
        <section className="generated-result-panel">
          <div className="generated-section-header">
            <div>
              <h5>Question Review</h5>
              <p className="muted-text">Review questions grouped by learning object.</p>
            </div>
          </div>
          <div className="question-review-list">
            {material.learning_objects.map((item, index) => {
              const questions = questionsByNode.get(item.id) || [];
              if (!questions.length) return null;
              return (
                <details className="question-node-group" key={item.id} open={index === 0}>
                  <summary>
                    <strong>{item.title}</strong>
                    <span className="muted-text">
                      {" "}&middot; {questions.length} question{questions.length === 1 ? "" : "s"}
                    </span>
                  </summary>
                  {questions.map((question) => (
                    <QuestionCard
                      key={question.id}
                      question={question}
                      onSaved={loadQuestionBank}
                      onError={onError}
                    />
                  ))}
                </details>
              );
            })}
          </div>
        </section>
      )}
      </>
      )}

      {activeTab === "audio" && (
      <section className="generated-result-panel">
        <div className="generated-section-header">
          <div>
            <h5>Lesson Playlist</h5>
            <p className="muted-text">
              Audio is generated from the confirmed learning objects, with each node&apos;s practice
              questions read after its lesson (answers are not spoken).
            </p>
          </div>
          <div className="generated-item-actions">
            <button
              className="btn btn-primary btn-small"
              type="button"
              disabled={!material.learning_objects.length || Boolean(busyAction)}
              onClick={() => generateAudio("lessons")}
            >
              {busyAction === "audio-lessons"
                ? "Generating lessons..."
                : lessonAudioGenerated
                  ? "Regenerate lesson audio"
                  : "Generate lesson audio"}
            </button>
            <button
              className="btn btn-secondary btn-small"
              type="button"
              disabled={!totalQuestions || Boolean(busyAction)}
              onClick={() => generateAudio("questions")}
            >
              {busyAction === "audio-questions"
                ? "Generating questions..."
                : questionAudioGenerated
                  ? "Regenerate question audio"
                  : "Generate question audio"}
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
                <span>{lessonAudioItems.filter((item) => item.audio_url).length} ready</span>
              </div>
              <div className="generated-list">
                {lessonAudioItems.map((item, index) => (
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

            <div className="audio-playlist-group">
              <div className="audio-playlist-group-header">
                <strong>Practice question audio</strong>
                <span>{questionAudioCount} ready</span>
              </div>
              {!questionAudioItems.length ? (
                <p className="muted-text">Generate questions first, then generate audio to create question tracks.</p>
              ) : (
                <div className="generated-list">
                  {questionAudioItems.map((item, index) => (
                    <div className="generated-item playlist-item" key={`${item.question_id || "question"}-${index}`}>
                      <span>{index + 1}</span>
                      <div>
                        <strong>{item.title || `Question audio ${index + 1}`}</strong>
                        <small>{(item.type || "practice question").replace(/_/g, " ")}</small>
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
            </div>
          </div>
        )}
      </section>
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
