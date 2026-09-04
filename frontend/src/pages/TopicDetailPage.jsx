import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  acceptLearningObjectMatchSuggestion,
  confirmLearningObjects,
  connectLearningObjects,
  createLearningObject,
  deleteLearningMaterial,
  deleteLearningObject,
  fetchCourse,
  fetchLearningResources,
  generateAudioPlaylist,
  rejectLearningObjectMatchSuggestion,
  reviewQuestionPairing,
  separateLearningObject,
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

function logLearningObjectMatchDebug(payload) {
  const suggestions = payload?.match_suggestions || [];
  const configuration = payload?.matching_debug || {};
  const weights = configuration.weights || {};
  const thresholds = configuration.thresholds || {};

  console.groupCollapsed(
    `[MAVIA matcher] ${suggestions.length} recommended pair${suggestions.length === 1 ? "" : "s"}`,
  );
  console.info("Matcher configuration", configuration);
  console.table(
    Object.entries(weights).map(([signal, weight]) => ({ signal, weight, percentage: `${Math.round(weight * 100)}%` })),
  );
  console.table(
    Object.entries(thresholds).map(([rule, threshold]) => ({ rule, threshold, percentage: `${Math.round(threshold * 100)}%` })),
  );

  suggestions.forEach((suggestion) => {
    const evidence = suggestion.evidence || {};
    const score = Number(suggestion.similarity_score ?? evidence.score ?? 0);
    const margin = Number(evidence.winner_margin ?? 0);
    const groupMemberScore = Number(evidence.minimum_group_member_score ?? score);
    console.groupCollapsed(
      `[Pair ${suggestion.id}] ${suggestion.source_learning_object?.title || "Untitled"} ↔ ${suggestion.candidate_learning_object?.title || "Untitled"}`,
    );
    console.table({
      overall_score: score,
      title_tfidf: Number(evidence.title_tfidf || 0),
      content_tfidf: Number(evidence.content_tfidf || 0),
      character_ngram: Number(evidence.character_ngram || 0),
      keyword_overlap: Number(evidence.keyword_overlap || 0),
      structure: Number(evidence.structure || 0),
      runner_up_score: Number(evidence.runner_up_score || 0),
      winner_margin: margin,
      minimum_group_member_score: groupMemberScore,
      exact_normalized_title: Boolean(evidence.exact_normalized_title),
    });
    console.table([
      {
        rule: "Teacher review score",
        actual: score,
        required: thresholds.teacher_review,
        passed: score >= Number(thresholds.teacher_review ?? 0),
      },
      {
        rule: "Automatic connection score",
        actual: score,
        required: thresholds.auto_connect,
        passed: score >= Number(thresholds.auto_connect ?? 0),
      },
      {
        rule: "Winner margin",
        actual: margin,
        required: thresholds.minimum_winner_margin,
        passed: margin >= Number(thresholds.minimum_winner_margin ?? 0),
      },
      {
        rule: "Every group member",
        actual: groupMemberScore,
        required: thresholds.minimum_group_member,
        passed: groupMemberScore >= Number(thresholds.minimum_group_member ?? 0),
      },
    ]);
    console.debug("Raw match suggestion", suggestion);
    console.groupEnd();
  });
  console.groupEnd();

  const questionPairings = payload?.question_pairings || [];
  const questionConfiguration = payload?.question_pairing_debug || {};
  const questionWeights = questionConfiguration.weights || {};
  const questionThresholds = questionConfiguration.thresholds || {};
  console.groupCollapsed(
    `[MAVIA question matcher] ${questionPairings.length} detected question${questionPairings.length === 1 ? "" : "s"}`,
  );
  console.info("Question pairing configuration", questionConfiguration);
  console.table(
    Object.entries(questionWeights).map(([signal, weight]) => ({
      signal,
      weight,
      percentage: `${Math.round(weight * 100)}%`,
    })),
  );
  console.table(
    Object.entries(questionThresholds).map(([rule, threshold]) => ({
      rule,
      threshold,
      percentage: `${Math.round(threshold * 100)}%`,
    })),
  );
  console.table(
    questionPairings.map((question) => {
      const link = question.learning_object_links?.[0];
      return {
        question_id: question.id,
        prompt: question.prompt,
        learning_object_id: link?.learning_object ?? null,
        group_id: link?.learning_object_group_id ?? null,
        score: link ? Number(link.relevance_score || 0) : null,
        status: link?.review_status || "unmatched",
        method: link?.method || questionConfiguration.method || "unknown",
      };
    }),
  );
  console.groupEnd();
}

function ReviewQueueNavigator({ index, count, onChange, disabled, itemLabel }) {
  if (!count) return null;
  return (
    <div className="review-queue-navigator" aria-label={`${itemLabel} navigation`}>
      <button
        type="button"
        disabled={disabled || index === 0}
        onClick={() => onChange(index - 1)}
      >
        Previous
      </button>
      <strong>{index + 1} of {count}</strong>
      <button
        type="button"
        disabled={disabled || index >= count - 1}
        onClick={() => onChange(index + 1)}
      >
        Next
      </button>
    </div>
  );
}

function ReviewQueuePanel({
  suggestions,
  questionPairings,
  groups,
  materialById,
  busyAction,
  onReview,
  onReviewQuestion,
}) {
  const [activeTab, setActiveTab] = useState(suggestions.length ? "objects" : "questions");
  const [editingQuestionId, setEditingQuestionId] = useState(null);
  const [selectedGroupId, setSelectedGroupId] = useState("");
  const [objectIndex, setObjectIndex] = useState(0);
  const [questionIndex, setQuestionIndex] = useState(0);
  const totalToReview = suggestions.length + questionPairings.length;

  useEffect(() => {
    if (activeTab === "objects" && !suggestions.length && questionPairings.length) {
      setActiveTab("questions");
    } else if (activeTab === "questions" && !questionPairings.length && suggestions.length) {
      setActiveTab("objects");
    }
  }, [activeTab, questionPairings.length, suggestions.length]);

  useEffect(() => {
    setObjectIndex((current) => Math.max(0, Math.min(current, suggestions.length - 1)));
  }, [suggestions.length]);

  useEffect(() => {
    setQuestionIndex((current) => Math.max(0, Math.min(current, questionPairings.length - 1)));
  }, [questionPairings.length]);

  const currentQuestionId = questionPairings[questionIndex]?.id;
  useEffect(() => {
    setEditingQuestionId(null);
    setSelectedGroupId("");
  }, [activeTab, currentQuestionId]);

  if (!totalToReview) return null;

  function beginConceptChange(question) {
    const link = question.learning_object_links?.[0];
    setEditingQuestionId(question.id);
    setSelectedGroupId(link?.learning_object_group_id ? String(link.learning_object_group_id) : "");
  }

  return (
    <aside className="match-suggestion-panel" aria-labelledby="match-suggestion-title">
      <div className="match-suggestion-heading">
        <div>
          <span className="connection-eyebrow">Review queue</span>
          <h4 id="match-suggestion-title">Teacher decisions</h4>
        </div>
        <span>{totalToReview} to review</span>
      </div>
      <div className="review-queue-tabs" role="tablist" aria-label="Review queue type">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "objects"}
          className={activeTab === "objects" ? "is-active" : ""}
          onClick={() => setActiveTab("objects")}
        >
          Object pairs <span>{suggestions.length}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "questions"}
          className={activeTab === "questions" ? "is-active" : ""}
          onClick={() => setActiveTab("questions")}
        >
          Question pairs <span>{questionPairings.length}</span>
        </button>
      </div>

      {activeTab === "objects" && (
        <>
          <p className="match-suggestion-intro">
            Review one pair at a time. Accept if both teach the same concept; decline if they do not.
          </p>
          {!suggestions.length ? (
            <div className="review-queue-empty">No learning-object pairs need review.</div>
          ) : (
            <>
              <ReviewQueueNavigator
                index={objectIndex}
                count={suggestions.length}
                onChange={setObjectIndex}
                disabled={Boolean(busyAction)}
                itemLabel="Object pair"
              />
              <div className="match-suggestion-list">
                {suggestions.map((suggestion, index) => {
                  const source = suggestion.source_learning_object;
                  const candidate = suggestion.candidate_learning_object;
                  const sourceMaterial = materialById.get(Number(source.material));
                  const candidateMaterial = materialById.get(Number(candidate.material));
                  return (
                    <article
                      className={`match-suggestion-card ${
                        index === objectIndex ? "" : "is-hidden"
                      }`.trim()}
                      key={suggestion.id}
                    >
                      <div className="match-suggestion-score">
                        <span>Similarity</span>
                        <strong>{Math.round(suggestion.similarity_score * 100)}%</strong>
                        <em>{suggestion.confidence} confidence</em>
                      </div>
                      <div className="match-suggestion-pair">
                        <div className="match-source-card">
                          <div className="match-source-label">
                            <span aria-hidden="true">A</span>
                            <small>{sourceMaterial?.filename || `PDF ${source.material}`}</small>
                          </div>
                          <strong>{source.title}</strong>
                          {source.image_url && (
                            <img
                              className="review-source-image"
                              src={source.image_url}
                              alt={source.title || "Source A"}
                            />
                          )}
                          <p className="match-source-content">
                            {source.content || "No narration content."}
                          </p>
                        </div>
                        <div className="match-pair-connector" aria-hidden="true">
                          <span>+</span>
                          <small>possible match</small>
                        </div>
                        <div className="match-source-card">
                          <div className="match-source-label">
                            <span aria-hidden="true">B</span>
                            <small>{candidateMaterial?.filename || `PDF ${candidate.material}`}</small>
                          </div>
                          <strong>{candidate.title}</strong>
                          {candidate.image_url && (
                            <img
                              className="review-source-image"
                              src={candidate.image_url}
                              alt={candidate.title || "Source B"}
                            />
                          )}
                          <p className="match-source-content">
                            {candidate.content || "No narration content."}
                          </p>
                        </div>
                      </div>
                      <div className="match-suggestion-actions">
                        <button
                          type="button"
                          className="btn btn-secondary btn-small"
                          disabled={Boolean(busyAction)}
                          onClick={() => onReview(suggestion, "reject")}
                        >
                          {busyAction === `suggestion-reject-${suggestion.id}`
                            ? "Declining..."
                            : "Decline"}
                        </button>
                        <button
                          type="button"
                          className="btn btn-primary btn-small"
                          disabled={Boolean(busyAction)}
                          onClick={() => onReview(suggestion, "accept")}
                        >
                          {busyAction === `suggestion-accept-${suggestion.id}`
                            ? "Accepting..."
                            : "Accept"}
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
            </>
          )}
        </>
      )}

      {activeTab === "questions" && (
        <>
          <p className="match-suggestion-intro">
            Review one uncertain question at a time. Accept the suggested concept, change it, or decline it.
          </p>
          {!questionPairings.length ? (
            <div className="review-queue-empty">No question pairs need review.</div>
          ) : (
            <>
              <ReviewQueueNavigator
                index={questionIndex}
                count={questionPairings.length}
                onChange={setQuestionIndex}
                disabled={Boolean(busyAction)}
                itemLabel="Question pair"
              />
              <div className="question-review-list">
                {questionPairings.map((question, index) => {
                  const link = question.learning_object_links?.[0];
                  const suggestedGroup = groups.find(
                    (group) => Number(group.id) === Number(link?.learning_object_group_id),
                  );
                  const suggestedObject = suggestedGroup?.learning_objects?.find(
                    (item) => Number(item.id) === Number(link?.learning_object),
                  ) || suggestedGroup?.learning_objects?.[0];
                  const suggestedLabel = suggestedGroup?.label
                    || suggestedGroup?.learning_objects?.[0]?.title
                    || link?.learning_object_title
                    || "No suggested concept";
                  const material = materialById.get(Number(question.material));
                  const suggestedMaterial = materialById.get(Number(suggestedObject?.material));
                  const isUnmatched = link?.review_status === "unmatched";
                  const isEditing = editingQuestionId === question.id;
                  return (
                  <article
                    className={`question-review-card ${index === questionIndex ? "" : "is-hidden"}`.trim()}
                    key={question.id}
                  >
                    <div className="question-review-meta">
                      <span className={`question-review-status ${isUnmatched ? "is-unmatched" : ""}`}>
                        {isUnmatched ? "No confident match" : "Needs review"}
                      </span>
                      <small title={material?.filename || ""}>
                        {material?.filename || material?.title || `PDF ${question.material}`}
                      </small>
                    </div>
                    <div className="question-review-prompt">
                      <span aria-hidden="true">Q</span>
                      <strong>{question.prompt}</strong>
                    </div>
                    <div className="question-review-suggestion">
                      <div className="question-review-suggestion-heading">
                        <small>{isUnmatched ? "Best available concept" : "Suggested concept"}</small>
                        {suggestedMaterial && <span>{suggestedMaterial.filename || suggestedMaterial.title}</span>}
                      </div>
                      <strong>{suggestedLabel}</strong>
                      {suggestedObject?.image_url && (
                        <img
                          className="review-source-image"
                          src={suggestedObject.image_url}
                          alt={suggestedObject.title || suggestedLabel}
                        />
                      )}
                      <p>{suggestedObject?.content || "No learning-object content is available."}</p>
                    </div>

                    {isEditing && (
                      <div className="question-concept-picker">
                        <label htmlFor={`question-concept-${question.id}`}>Choose the correct concept</label>
                        <select
                          id={`question-concept-${question.id}`}
                          value={selectedGroupId}
                          onChange={(event) => setSelectedGroupId(event.target.value)}
                        >
                          <option value="">Select a concept...</option>
                          {groups.map((group) => (
                            <option value={group.id} key={group.id}>
                              {group.label || group.learning_objects?.[0]?.title || `Concept ${group.id}`}
                            </option>
                          ))}
                        </select>
                        <div className="question-concept-picker-actions">
                          <button
                            type="button"
                            className="btn btn-secondary btn-small"
                            disabled={Boolean(busyAction)}
                            onClick={() => setEditingQuestionId(null)}
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            className="btn btn-primary btn-small"
                            disabled={!selectedGroupId || Boolean(busyAction)}
                            onClick={async () => {
                              const completed = await onReviewQuestion(
                                question,
                                "change",
                                selectedGroupId,
                              );
                              if (completed) setEditingQuestionId(null);
                            }}
                          >
                            {busyAction === `question-change-${question.id}`
                              ? "Saving..."
                              : "Save concept"}
                          </button>
                        </div>
                      </div>
                    )}

                    {!isEditing && (
                      <div className="question-review-actions">
                        <button
                          type="button"
                          className="btn btn-secondary btn-small"
                          disabled={Boolean(busyAction)}
                          onClick={() => onReviewQuestion(question, "unpair")}
                        >
                          {busyAction === `question-unpair-${question.id}`
                            ? "Declining..."
                            : "Decline"}
                        </button>
                        <button
                          type="button"
                          className="btn btn-secondary btn-small"
                          disabled={Boolean(busyAction)}
                          onClick={() => beginConceptChange(question)}
                        >
                          Change concept
                        </button>
                        <button
                          type="button"
                          className="btn btn-primary btn-small"
                          disabled={!link || Boolean(busyAction)}
                          onClick={() => onReviewQuestion(question, "confirm")}
                        >
                          {busyAction === `question-confirm-${question.id}`
                            ? "Accepting..."
                            : "Accept"}
                        </button>
                      </div>
                    )}
                    </article>
                  );
                })}
              </div>
            </>
          )}
        </>
      )}
    </aside>
  );
}

function LearningObjectConnections({ courseId, topicId, materials, onError, onMessage }) {
  const [resources, setResources] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState("");
  const [filter, setFilter] = useState("connected");
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedIds, setSelectedIds] = useState([]);
  const [groupLabel, setGroupLabel] = useState("");

  const materialSignature = useMemo(
    () => materials
      .map((material) => `${material.id}:${material.learning_objects.map((item) => `${item.id}:${item.group}:${item.title}:${(item.content || "").length}`).join(",")}`)
      .join("|"),
    [materials],
  );

  useEffect(() => {
    let cancelled = false;

    async function loadResources() {
      setLoading(true);
      try {
        const data = await fetchLearningResources(courseId, topicId);
        if (!cancelled) {
          setResources(data);
          setSelectedIds([]);
        }
      } catch (err) {
        if (!cancelled) onError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadResources();
    return () => {
      cancelled = true;
    };
  }, [courseId, topicId, materialSignature, onError]);

  useEffect(() => {
    if (resources) logLearningObjectMatchDebug(resources);
  }, [resources]);

  const groups = resources?.learning_object_groups || [];
  const matchSuggestions = resources?.match_suggestions || [];
  const allQuestionPairings = resources?.question_pairings || [];
  const questionReviewQueue = allQuestionPairings.filter((question) => {
    const status = question.learning_object_links?.[0]?.review_status;
    return !status || status === "pending_review" || status === "unmatched";
  });
  const totalReviewCount = matchSuggestions.length + questionReviewQueue.length;
  const materialById = useMemo(
    () => new Map(materials.map((material) => [Number(material.id), material])),
    [materials],
  );
  const groupByObjectId = useMemo(() => {
    const result = new Map();
    groups.forEach((group) => {
      group.learning_objects.forEach((item) => result.set(item.id, group.id));
    });
    return result;
  }, [groups]);
  const connectedGroups = groups.filter((group) => group.learning_objects.length > 1);
  const singletonGroups = groups.filter((group) => group.learning_objects.length === 1);
  const visibleGroups = groups.filter((group) => {
    if (filter === "connected" && group.learning_objects.length <= 1) return false;
    if (filter === "single" && group.learning_objects.length !== 1) return false;
    const query = searchTerm.trim().toLocaleLowerCase();
    if (!query) return true;
    const searchableText = [
      group.label,
      ...group.learning_objects.flatMap((item) => [
        item.title,
        item.content,
        item.section_title,
        materialById.get(Number(item.material))?.filename,
      ]),
      ...group.questions.map((question) => question.prompt),
    ].filter(Boolean).join(" ").toLocaleLowerCase();
    return searchableText.includes(query);
  });
  const selectedGroupCount = new Set(selectedIds.map((id) => groupByObjectId.get(id))).size;
  const pairedQuestionCount = new Set(
    groups.flatMap((group) => group.questions.map((question) => question.id)),
  ).size;

  function toggleSelection(objectId) {
    setSelectedIds((current) => (
      current.includes(objectId)
        ? current.filter((id) => id !== objectId)
        : [...current, objectId]
    ));
  }

  async function connectSelected() {
    if (selectedIds.length < 2) return;
    setBusyAction("connect");
    onError("");
    onMessage("");
    try {
      const data = await connectLearningObjects(courseId, topicId, selectedIds, groupLabel);
      setResources(data);
      setSelectedIds([]);
      setGroupLabel("");
      setFilter("connected");
      onMessage("Selected learning objects are now connected as content variations.");
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  async function separateObject(item) {
    setBusyAction(`separate-${item.id}`);
    onError("");
    onMessage("");
    try {
      const data = await separateLearningObject(courseId, topicId, item.id);
      setResources(data);
      setSelectedIds((current) => current.filter((id) => id !== item.id));
      onMessage(`“${item.title}” is now a separate learning object group.`);
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  async function reviewMatchSuggestion(suggestion, decision) {
    setBusyAction(`suggestion-${decision}-${suggestion.id}`);
    onError("");
    onMessage("");
    try {
      const action = decision === "accept"
        ? acceptLearningObjectMatchSuggestion
        : rejectLearningObjectMatchSuggestion;
      const data = await action(courseId, topicId, suggestion.id);
      setResources(data);
      onMessage(
        decision === "accept"
          ? "Suggested learning objects were connected."
          : "Suggested connection was rejected and the objects remain separate.",
      );
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  async function reviewQuestion(question, decision, learningObjectGroupId = null) {
    setBusyAction(`question-${decision}-${question.id}`);
    onError("");
    onMessage("");
    try {
      const data = await reviewQuestionPairing(
        courseId,
        topicId,
        question.id,
        decision,
        learningObjectGroupId,
      );
      setResources(data);
      if (decision === "confirm") {
        onMessage("Question paired with the suggested concept.");
      } else if (decision === "change") {
        onMessage("Question moved to the selected concept.");
      } else {
        onMessage("Question left unpaired for the learning-path module.");
      }
      return true;
    } catch (err) {
      onError(err.message);
      return false;
    } finally {
      setBusyAction("");
    }
  }

  return (
    <div className={`connection-review-layout ${totalReviewCount > 0 ? "has-recommendations" : ""}`}>
      <section className="connection-review-panel" aria-labelledby="connection-review-title">
      <div className="connection-review-heading">
        <div>
          <span className="connection-eyebrow">Teacher review</span>
          <h3 id="connection-review-title">Connected Learning Objects</h3>
          <p>
            Review learning objects from every PDF and connect equivalent content into one concept group.
            Each object remains a separate variation for the learning-path module.
          </p>
        </div>
        <span className="connection-source-count">
          {materials.length} source PDF{materials.length === 1 ? "" : "s"}
        </span>
      </div>

      {loading ? (
        <div className="connection-empty">Checking learning-object connections…</div>
      ) : (
        <>
          <div className="connection-summary" aria-label="Connection summary">
            <div><strong>{groups.length}</strong><span>Concept groups</span></div>
            <div><strong>{connectedGroups.length}</strong><span>Connected</span></div>
            <div><strong>{singletonGroups.length}</strong><span>Single-source</span></div>
            <div><strong>{totalReviewCount}</strong><span>To review</span></div>
            <div><strong>{pairedQuestionCount}</strong><span>Paired questions</span></div>
          </div>

          <div className="connection-review-main">
          <div className="connection-toolbar">
            <div className="connection-filters" aria-label="Filter learning-object groups">
              {[
                ["connected", "Connected", connectedGroups.length],
                ["single", "Single only", singletonGroups.length],
                ["all", "All groups", groups.length],
              ].map(([value, label, count]) => (
                <button
                  key={value}
                  type="button"
                  className={filter === value ? "is-active" : ""}
                  aria-pressed={filter === value}
                  onClick={() => setFilter(value)}
                >
                  {label} <span>{count}</span>
                </button>
              ))}
            </div>
            <div className="connection-toolbar-tools">
              <label className="connection-search">
                <span className="sr-only">Search learning-object groups</span>
                <input
                  type="search"
                  value={searchTerm}
                  placeholder="Search concept or PDF"
                  onChange={(event) => setSearchTerm(event.target.value)}
                />
              </label>
              <span className="connection-selection-count" aria-live="polite">
                {selectedIds.length} selected
              </span>
            </div>
          </div>

          {!visibleGroups.length ? (
            <div className="connection-empty">
              {filter === "connected"
                ? "No connected groups match this view. Open “Single only” and connect equivalent objects."
                : "No learning-object groups match this filter."}
            </div>
          ) : (
            <div className="connection-group-list">
              {visibleGroups.map((group) => {
                const sourceIds = new Set(group.learning_objects.map((item) => item.material));
                const isConnected = group.learning_objects.length > 1;
                return (
                  <article className={`connection-group-card ${isConnected ? "is-connected" : ""}`} key={group.id}>
                    <header>
                      <div>
                        <span className="connection-group-id">Group {group.id}</span>
                        <h4>{group.label || group.learning_objects[0]?.title || "Untitled concept"}</h4>
                      </div>
                      <span className={`connection-status ${isConnected ? "is-connected" : "is-single"}`}>
                        {isConnected
                          ? `${group.learning_objects.length} variations · ${sourceIds.size} PDF${sourceIds.size === 1 ? "" : "s"}`
                          : "Single variation"}
                      </span>
                    </header>

                    <div className="connection-object-list">
                      {group.learning_objects.map((item) => {
                        const material = materialById.get(Number(item.material));
                        const isImage = isImageLearningObject(item);
                        const isMissingImageDescription = isImage && !item.content?.trim();
                        return (
                          <div className="connection-object-row" key={item.id}>
                            <label className="connection-object-select">
                              <input
                                type="checkbox"
                                checked={selectedIds.includes(item.id)}
                                disabled={Boolean(busyAction)}
                                onChange={() => toggleSelection(item.id)}
                              />
                              <span className="sr-only">Select {item.title}</span>
                            </label>
                            <div className="connection-object-copy">
                              <div className="connection-object-title-row">
                                <span className="connection-object-order">{Number(item.order) + 1}</span>
                                <strong>{item.title}</strong>
                                <span className="connection-object-source">
                                  {material?.filename || material?.title || `PDF ${item.material}`}
                                </span>
                              </div>
                              {isImage && item.image_url && (
                                <figure className="learning-object-image-preview connection-object-image">
                                  <img src={item.image_url} alt={item.title || "Image learning object"} />
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
                                      ? "No teacher image description is available."
                                      : "Teacher image description included."}
                                  </span>
                                </div>
                              )}
                              <p
                                className={`learning-object-content-text connection-object-full-content ${
                                  isImage ? "image-description-text" : ""
                                }`.trim()}
                              >
                                {item.content || "No narration content."}
                              </p>
                              <small>
                                {item.kind === "image" ? "Image learning object" : "Text learning object"}
                                {item.section_title ? ` · Section: ${item.section_title}` : ""}
                              </small>
                            </div>
                            {isConnected && (
                              <button
                                type="button"
                                className="btn btn-secondary btn-small"
                                disabled={Boolean(busyAction)}
                                onClick={() => separateObject(item)}
                              >
                                {busyAction === `separate-${item.id}` ? "Separating…" : "Separate"}
                              </button>
                            )}
                          </div>
                        );
                      })}
                    </div>

                    {group.questions.length > 0 && (
                      <div className="connection-question-list">
                        <h5>Questions paired with this concept</h5>
                        {group.questions.map((question) => {
                          const link = question.learning_object_links.find(
                            (item) => Number(item.learning_object_group_id) === Number(group.id),
                          );
                          const pairingLabel = link?.review_status === "teacher_confirmed"
                            ? "Teacher confirmed"
                            : "Automatically paired";
                          return (
                            <div className="connection-question-row" key={question.id}>
                              <span aria-hidden="true">Q</span>
                              <div>
                                <strong>{question.prompt}</strong>
                                <small>
                                  {pairingLabel} with {link?.learning_object_title || "this concept"}
                                </small>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          )}
          </div>
        </>
      )}

      {!loading && selectedIds.length > 0 && (
        <aside className="connection-selection-dock" aria-label="Selected learning objects">
          <div className="connection-selection-dock-heading">
            <div>
              <strong>{selectedIds.length} object{selectedIds.length === 1 ? "" : "s"} selected</strong>
              <small>
                {selectedIds.length < 2
                  ? "Select one more learning object."
                  : selectedGroupCount < 2
                    ? "These objects already belong to one group."
                    : "Ready to connect as variations."}
              </small>
            </div>
            <button
              type="button"
              className="connection-clear-selection"
              disabled={Boolean(busyAction)}
              onClick={() => setSelectedIds([])}
            >
              Clear
            </button>
          </div>
          <label>
            Group label <span>(optional)</span>
            <input
              value={groupLabel}
              disabled={busyAction === "connect"}
              placeholder="Example: Shape"
              onChange={(event) => setGroupLabel(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="btn btn-primary"
            disabled={selectedIds.length < 2 || Boolean(busyAction)}
            onClick={connectSelected}
          >
            {busyAction === "connect" ? "Connecting…" : "Connect selected objects"}
          </button>
        </aside>
      )}
      </section>
      <ReviewQueuePanel
        suggestions={matchSuggestions}
        questionPairings={questionReviewQueue}
        groups={groups}
        materialById={materialById}
        busyAction={busyAction}
        onReview={reviewMatchSuggestion}
        onReviewQuestion={reviewQuestion}
      />
    </div>
  );
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
  const [showDeleteMaterialConfirm, setShowDeleteMaterialConfirm] = useState(false);
  const [learningObjectToDelete, setLearningObjectToDelete] = useState(null);

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
    setBusyAction(`delete-${objectId}`);
    onError("");
    onMessage("");
    try {
      const updatedCourse = await deleteLearningObject(courseId, material.id, objectId);
      onCourseChange(updatedCourse);
      const updatedMaterial = updatedCourse.materials?.find((item) => item.id === material.id);
      setSelectedId(updatedMaterial?.learning_objects?.[0]?.id || null);
      setLearningObjectToDelete(null);
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

  async function removeMaterial() {
    setBusyAction("delete-material");
    onError("");
    onMessage("");
    try {
      const updatedCourse = await deleteLearningMaterial(courseId, material.id);
      onCourseChange(updatedCourse);
      setShowDeleteMaterialConfirm(false);
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
          className="btn btn-danger btn-small"
          type="button"
          disabled={Boolean(busyAction)}
          onClick={() => setShowDeleteMaterialConfirm(true)}
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
                      onClick={() => setLearningObjectToDelete(selectedObject)}
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
                            <p
                              className={`learning-object-content-text ${
                                isImage ? "image-description-text" : ""
                              }`.trim()}
                            >
                              {item.content}
                            </p>
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

      {showDeleteMaterialConfirm && (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && busyAction !== "delete-material") {
              setShowDeleteMaterialConfirm(false);
            }
          }}
        >
          <div
            className="modal-card delete-material-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby={`delete-material-${material.id}`}
          >
            <div className="delete-modal-heading">
              <span className="delete-modal-icon" aria-hidden="true">!</span>
              <div>
                <h3 id={`delete-material-${material.id}`}>Delete lesson material?</h3>
                <p>This action cannot be undone.</p>
              </div>
            </div>
            <div className="delete-material-summary">
              <strong>{material.title}</strong>
              <span>{material.filename}</span>
            </div>
            <p>
              The uploaded PDF, extracted content, learning objects, and generated audio will be
              permanently removed.
            </p>
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-secondary"
                disabled={busyAction === "delete-material"}
                onClick={() => setShowDeleteMaterialConfirm(false)}
              >
                Keep material
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={busyAction === "delete-material"}
                onClick={removeMaterial}
              >
                {busyAction === "delete-material" ? "Deleting..." : "Delete material"}
              </button>
            </div>
          </div>
        </div>
      )}

      {learningObjectToDelete && (
        <div className="modal-backdrop" role="presentation">
          <div
            className="modal-card"
            role="dialog"
            aria-modal="true"
            aria-labelledby={`delete-learning-object-${material.id}`}
          >
            <h3 id={`delete-learning-object-${material.id}`}>Delete learning object?</h3>
            <p>
              This will permanently remove <strong>“{learningObjectToDelete.title}”</strong> from this
              lesson. You will need to confirm the learning objects again afterward.
            </p>
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-secondary"
                disabled={busyAction === `delete-${learningObjectToDelete.id}`}
                onClick={() => setLearningObjectToDelete(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={busyAction === `delete-${learningObjectToDelete.id}`}
                onClick={() => removeLearningObject(learningObjectToDelete.id)}
              >
                {busyAction === `delete-${learningObjectToDelete.id}` ? "Deleting..." : "Delete object"}
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
  const [activeSource, setActiveSource] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

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
  const materials = useMemo(
    () => (course?.materials || []).filter(
      (material) => String(material.outline_node) === String(topicId),
    ),
    [course?.materials, topicId],
  );
  const selectedMaterial = materials.find(
    (material) => String(material.id) === String(activeSource),
  );

  useEffect(() => {
    if (!materials.length) {
      setActiveSource("");
      return;
    }
    setActiveSource((current) => {
      if (current === "connections") return current;
      if (materials.some((material) => String(material.id) === String(current))) {
        return current;
      }
      return String(materials[0].id);
    });
  }, [materials]);

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
      if (uploadedMaterial) setActiveSource(String(uploadedMaterial.id));
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
    <section className={`topic-workspace-shell ${sidebarCollapsed ? "is-sidebar-collapsed" : ""}`}>
      <aside className="card lesson-pdf-sidebar" aria-label="Lesson PDF navigation">
        <div className="lesson-sidebar-brand-row">
          <Link to="/" className="lesson-sidebar-brand" title="Mavia home">
            <span className="lesson-sidebar-brand-mark" aria-hidden="true">M</span>
            <span className="lesson-sidebar-brand-copy">
              <strong>Mavia</strong>
              <small>Lesson material workspace</small>
            </span>
          </Link>
          <button
            type="button"
            className="sidebar-collapse-toggle"
            aria-label={sidebarCollapsed ? "Expand PDF sidebar" : "Collapse PDF sidebar"}
            aria-expanded={!sidebarCollapsed}
            onClick={() => setSidebarCollapsed((current) => !current)}
          >
            <span aria-hidden="true">{sidebarCollapsed ? ">" : "<"}</span>
          </button>
        </div>

        {materials.length > 0 && (
          <nav className="lesson-pdf-navigation" aria-label="Uploaded lesson PDFs">
            <button
              type="button"
              className={`lesson-pdf-nav-item connection-nav-item ${activeSource === "connections" ? "is-active" : ""}`}
              aria-current={activeSource === "connections" ? "page" : undefined}
              title={sidebarCollapsed ? "Review connections" : undefined}
              onClick={() => setActiveSource("connections")}
            >
              <span className="lesson-pdf-short-label" aria-hidden="true">R</span>
              <span className="lesson-pdf-nav-copy">
                <strong>Review connections</strong>
              </span>
            </button>

            <div className="lesson-pdf-nav-label">Uploaded files</div>
            <ul className="lesson-pdf-list">
              {materials.map((material, index) => {
                const isActive = String(activeSource) === String(material.id);
                return (
                  <li key={material.id}>
                    <button
                      type="button"
                      className={`lesson-pdf-nav-item ${isActive ? "is-active" : ""}`}
                      aria-current={isActive ? "page" : undefined}
                      title={material.filename || material.title}
                      onClick={() => setActiveSource(String(material.id))}
                    >
                      <span className="lesson-pdf-short-label" aria-hidden="true">{index + 1}</span>
                      <span className="lesson-pdf-nav-copy">
                        <strong>{material.title || `Lesson PDF ${index + 1}`}</strong>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </nav>
        )}
      </aside>

      <main className="topic-detail-page">
        <section className="card topic-detail-overview-card">
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
        </section>

        {error && <div className="error-banner">{error}</div>}
        {message && <div className="success-banner">{message}</div>}

        {!materials.length ? (
          <div className="empty-state">No learning material is stored under this topic yet.</div>
        ) : activeSource === "connections" ? (
          <LearningObjectConnections
            courseId={courseId}
            topicId={topicId}
            materials={materials}
            onError={setError}
            onMessage={setMessage}
          />
        ) : selectedMaterial ? (
          <div className="topic-material-list">
            <MaterialCard
              key={selectedMaterial.id}
              material={selectedMaterial}
              courseId={courseId}
              onCourseChange={setCourse}
              onError={setError}
              onMessage={setMessage}
            />
          </div>
        ) : (
          <div className="empty-state">Choose a lesson PDF from the sidebar.</div>
        )}
      </main>
    </section>
  );
}
