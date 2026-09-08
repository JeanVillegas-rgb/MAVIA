import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { uploadedMaterialFromResponse } from "../uploadNavigation";
import {
  acceptLearningObjectMatchSuggestion,
  confirmLearningObjects,
  connectLearningObjects,
  createLearningObject,
  createTopicQuestion,
  deleteLearningMaterial,
  deleteLearningObject,
  deleteTopicLearningObject,
  deleteTopicQuestion,
  fetchCourse,
  fetchLearningResources,
  fetchQuestionGenerationTrace,
  generateAudioPlaylist,
  publishTopic,
  rejectLearningObjectMatchSuggestion,
  reviewQuestionPairing,
  separateLearningObject,
  startQuestionGeneration,
  updateLearningObject,
  updateTopicQuestion,
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

function isQuestionMaterial(material) {
  const generatedJson = material?.generated_json || {};
  return generatedJson.document_role === "assessment"
    || (!(material?.learning_objects?.length) && Boolean(material?.questions?.length));
}

function renderInlineFormatting(text) {
  return String(text || "").split(/(\*\*[^*]+\*\*)/g).filter(Boolean).map((part, index) => (
    part.startsWith("**") && part.endsWith("**")
      ? <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong>
      : <Fragment key={`${part}-${index}`}>{part}</Fragment>
  ));
}

function FormattedLearningObjectContent({ content, className = "" }) {
  const lines = String(content || "No narration content.")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const blocks = [];
  let list = [];
  let listType = "ul";
  let implicitListMode = false;

  function flushList() {
    if (!list.length) return;
    const ListTag = listType;
    blocks.push(
      <ListTag className="formatted-content-list" key={`list-${blocks.length}`}>
        {list.map((item, index) => <li key={`${item}-${index}`}>{renderInlineFormatting(item)}</li>)}
      </ListTag>,
    );
    list = [];
  }

  lines.forEach((line) => {
    const bullet = line.match(/^(?:[-*\u2022])\s+(.+)$/);
    const numbered = line.match(/^\d+[.)]\s+(.+)$/);
    const isHeading = line.endsWith(":") && line.length <= 90;
    if (bullet || numbered) {
      const nextType = numbered ? "ol" : "ul";
      if (list.length && listType !== nextType) flushList();
      listType = nextType;
      list.push((bullet || numbered)[1]);
      return;
    }
    if (isHeading) {
      flushList();
      blocks.push(
        <p className="formatted-content-heading" key={`line-${blocks.length}`}>
          <strong>{renderInlineFormatting(line)}</strong>
        </p>,
      );
      implicitListMode = true;
      return;
    }
    if (implicitListMode) {
      listType = "ul";
      list.push(line);
      return;
    }
    flushList();
    blocks.push(
      <p key={`line-${blocks.length}`}>
        {renderInlineFormatting(line)}
      </p>,
    );
  });
  flushList();

  return <div className={`formatted-learning-object-content ${className}`.trim()}>{blocks}</div>;
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
      method: evidence.method || configuration.method || "unknown",
      overall_score: score,
      sbert_cosine: Number(evidence.sbert_cosine || 0),
      cross_encoder_score: Number(evidence.score || 0),
      members_checked: Number(evidence.members_checked || 0),
      all_members_checked: Boolean(evidence.all_members_checked),
      title_tfidf: Number(evidence.title_tfidf || 0),
      content_tfidf: Number(evidence.content_tfidf || 0),
      character_ngram: Number(evidence.character_ngram || 0),
      keyword_overlap: Number(evidence.keyword_overlap || 0),
      structure: Number(evidence.structure || 0),
      content_support: Number(evidence.content_support || 0),
      runner_up_score: Number(evidence.runner_up_score || 0),
      winner_margin: margin,
      minimum_group_member_score: groupMemberScore,
      minimum_group_content_support: Number(evidence.minimum_group_content_support || 0),
      exact_normalized_title: Boolean(evidence.exact_normalized_title),
    });
    const ruleResult = (actual, required) => ({
      actual,
      required: required ?? "not enabled",
      passed: required == null ? false : actual >= Number(required),
    });
    console.table([
      {
        rule: "Teacher review score",
        ...ruleResult(score, thresholds.teacher_review),
      },
      {
        rule: "Automatic connection score",
        ...ruleResult(score, thresholds.auto_connect),
      },
      {
        rule: "SBERT group-member similarity",
        ...ruleResult(
          Number(evidence.minimum_group_sbert_cosine || 0),
          thresholds.minimum_sbert_cosine,
        ),
      },
      {
        rule: "Winner margin",
        ...ruleResult(margin, thresholds.minimum_winner_margin),
      },
      {
        rule: "Every group member",
        ...ruleResult(groupMemberScore, thresholds.minimum_group_member),
      },
      {
        rule: "Content evidence",
        ...ruleResult(Number(evidence.content_support || 0), thresholds.minimum_content_support),
      },
      {
        rule: "Every member content",
        ...ruleResult(Number(evidence.minimum_group_content_support || 0), thresholds.minimum_content_support),
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

function ObjectPairsPanel({
  suggestions,
  materialById,
  busyAction,
  pendingQuestionCount,
  onReviewStepChange,
  onReview,
}) {
  const [objectIndex, setObjectIndex] = useState(0);

  useEffect(() => {
    setObjectIndex((current) => Math.max(0, Math.min(current, suggestions.length - 1)));
  }, [suggestions.length]);

  return (
    <aside className="match-suggestion-panel panel-aside" aria-labelledby="object-pairs-panel-title">
      <div className="match-suggestion-heading">
        <div>
          <span className="connection-eyebrow">Review queue</span>
          <h4 id="object-pairs-panel-title">Review object pairs</h4>
        </div>
        <span>{suggestions.length} to review</span>
      </div>
      <div className="review-step-indicator has-three-steps" aria-label="Review progress">
        <span className="is-active">1</span>
        <div aria-hidden="true" />
        <span>2</span>
        <div aria-hidden="true" />
        <span>3</span>
        <strong>Object pairs</strong>
      </div>
      <p className="match-suggestion-intro">Review one pair at a time. Accept if both teach the same concept; decline if they do not.</p>
      {!suggestions.length ? (
        <div className="review-queue-empty">No learning-object pairs need review.</div>
      ) : (
        <>
          <ReviewQueueNavigator index={objectIndex} count={suggestions.length} onChange={setObjectIndex} disabled={Boolean(busyAction)} itemLabel="Object pair" />
          <div className="match-suggestion-list">
            {suggestions.map((suggestion, index) => {
              const source = suggestion.source_learning_object;
              const candidate = suggestion.candidate_learning_object;
              const sourceMaterial = materialById.get(Number(source.material));
              const candidateMaterial = materialById.get(Number(candidate.material));
              return (
                <article className={`match-suggestion-card ${index === objectIndex ? "" : "is-hidden"}`.trim()} key={suggestion.id}>
                  <div className="match-suggestion-score">
                    <span>Similarity</span>
                    <strong>{Math.round(suggestion.similarity_score * 100)}%</strong>
                    <em>{suggestion.confidence} confidence</em>
                  </div>
                  <div className="match-suggestion-pair">
                    <div className="match-source-card">
                      <div className="match-source-label"><span aria-hidden="true">A</span><small>{sourceMaterial?.filename || `PDF ${source.material}`}</small></div>
                      <strong>{source.title}</strong>
                      {source.image_url && <img className="review-source-image" src={source.image_url} alt={source.title || "Source A"} />}
                      <p className="match-source-content">{source.content || "No narration content."}</p>
                    </div>
                    <div className="match-pair-connector" aria-hidden="true"><span>+</span><small>possible match</small></div>
                    <div className="match-source-card">
                      <div className="match-source-label"><span aria-hidden="true">B</span><small>{candidateMaterial?.filename || `PDF ${candidate.material}`}</small></div>
                      <strong>{candidate.title}</strong>
                      {candidate.image_url && <img className="review-source-image" src={candidate.image_url} alt={candidate.title || "Source B"} />}
                      <p className="match-source-content">{candidate.content || "No narration content."}</p>
                    </div>
                  </div>
                  <div className="match-suggestion-actions">
                    <button type="button" className="btn btn-secondary btn-small" disabled={Boolean(busyAction)} onClick={() => onReview(suggestion, "reject")}>{busyAction === `suggestion-reject-${suggestion.id}` ? "Declining..." : "Decline"}</button>
                    <button type="button" className="btn btn-primary btn-small" disabled={Boolean(busyAction)} onClick={() => onReview(suggestion, "accept")}>{busyAction === `suggestion-accept-${suggestion.id}` ? "Accepting..." : "Accept"}</button>
                  </div>
                </article>
              );
            })}
          </div>
        </>
      )}
      <div className="review-step-actions review-step-actions-next">
        <button type="button" className="btn btn-primary" disabled={Boolean(busyAction)} onClick={() => onReviewStepChange("questions")}>Next step: Question pairs ({pendingQuestionCount})</button>
      </div>
    </aside>
  );
}


function ReviewQueuePanel({
  questionPairings,
  groups,
  materialById,
  busyAction,
  onReviewStepChange,
  onReviewQuestion,
  onEditQuestion,
}) {
  const [editingQuestionId, setEditingQuestionId] = useState(null);
  const [editingContentId, setEditingContentId] = useState(null);
  const [selectedGroupId, setSelectedGroupId] = useState("");
  const [questionIndex, setQuestionIndex] = useState(0);

  useEffect(() => {
    setQuestionIndex((current) => Math.max(0, Math.min(current, questionPairings.length - 1)));
  }, [questionPairings.length]);

  const currentQuestionId = questionPairings[questionIndex]?.id;
  useEffect(() => {
    setEditingQuestionId(null);
    setSelectedGroupId("");
  }, [currentQuestionId]);

  function beginConceptChange(question) {
    const link = question.learning_object_links?.[0];
    setEditingQuestionId(question.id);
    setSelectedGroupId(link?.learning_object_group_id ? String(link.learning_object_group_id) : "");
  }

  return (
    <section className="connection-review-panel" aria-labelledby="match-suggestion-title">
      <div className="connection-review-heading">
        <div>
          <span className="connection-eyebrow">Review queue</span>
          <h3 id="match-suggestion-title">Review question pairs</h3>
          <p>
            Review one uncertain question at a time. Accept the suggested concept, change it, or decline it.
          </p>
        </div>
        <span className="connection-source-count">{questionPairings.length} to review</span>
      </div>
      <div className="review-step-indicator has-three-steps" aria-label="Review progress">
        <span className="is-complete">1</span>
        <div aria-hidden="true" />
        <span className="is-active">2</span>
        <div aria-hidden="true" />
        <span>3</span>
        <strong>Question pairs</strong>
      </div>

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
          const pairingStatus = link?.review_status || "unmatched";
          const isApproved = ["auto_confirmed", "teacher_confirmed"].includes(pairingStatus);
          return (
          <article
            className={`question-review-card ${index === questionIndex ? "" : "is-hidden"}`.trim()}
            key={question.id}
          >
            <div className="question-review-meta">
              <span className={`question-review-status ${isUnmatched ? "is-unmatched" : ""}`}>
                {isApproved ? "Approved" : isUnmatched ? "No confident match" : "Needs review"}
              </span>
              <span className={`question-origin-pill is-${question.source_type || "pdf"}`}>
                {question.source_type === "manual" ? "Manual" : question.source_type === "generated" ? "Generated" : "PDF"}
              </span>
              {question.thinking_order && <span className="question-thinking-pill">{question.thinking_order}</span>}
              <small title={material?.filename || ""}>
                {material?.filename || material?.title || `PDF ${question.material}`}
              </small>
            </div>
            <div className="question-review-prompt">
              <span aria-hidden="true">Q</span>
              <strong>{question.prompt}</strong>
            </div>
            {question.validation_status === "needs_review" && (
              <div className="question-validation-warning" role="alert"><strong>Question details required</strong><ul>{(question.validation_issues || []).map((issue) => <li key={issue}>{issue}</li>)}</ul></div>
            )}
            {question.choices?.length > 0 && editingContentId !== question.id && <ul className="question-review-choices">{question.choices.map((choice, choiceIndex) => <li key={choiceIndex}>{choice}</li>)}</ul>}
            {editingContentId === question.id && (
              <QuestionEditForm key={question.id} question={question} busy={Boolean(busyAction)} onCancel={() => setEditingContentId(null)} onSave={async (values) => { const saved = await onEditQuestion(question, values); if (saved) setEditingContentId(null); }} />
            )}
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
                      {group.label || group.learning_objects?.[0]?.title || "Untitled concept"}
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
                <button type="button" className="btn btn-secondary btn-small" disabled={Boolean(busyAction)} onClick={() => setEditingContentId(question.id)}>Edit question</button>
                {!isApproved && (
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
                )}
                {!isApproved && <button
                  type="button"
                  className="btn btn-secondary btn-small"
                  disabled={Boolean(busyAction)}
                  onClick={() => beginConceptChange(question)}
                >
                  Change concept
                </button>}
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
      <div className="review-step-actions-row">
        <button
          type="button"
          className="btn btn-secondary"
          disabled={Boolean(busyAction)}
          onClick={() => onReviewStepChange("objects")}
        >
          Back to object pairs
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={Boolean(busyAction)}
          onClick={() => onReviewStepChange("publish")}
        >
          Next step: Publish
        </button>
      </div>
    </section>
  );
}

function QuestionEditForm({ question, busy, onCancel, onSave }) {
  const [prompt, setPrompt] = useState(question.prompt || "");
  const [type, setType] = useState(question.question_type === "open_ended" ? "multiple_choice" : question.question_type);
  const [choices, setChoices] = useState(() => [...(question.choices || []), "", "", "", ""].slice(0, 4));
  const [answer, setAnswer] = useState(question.correct_answer || "");
  function changeType(next) { setType(next); if (next === "true_false") { setChoices(["True", "False", "", ""]); setAnswer(["true", "false"].includes(answer.toLowerCase()) ? answer : "True"); } }
  return (
    <form className="question-inline-editor" onSubmit={(event) => { event.preventDefault(); onSave({ prompt: prompt.trim(), question_type: type, choices: type === "true_false" ? ["True", "False"] : choices.filter((choice) => choice.trim()), correct_answer: answer }); }}>
      <label>Question<textarea required rows="3" value={prompt} onChange={(event) => setPrompt(event.target.value)} /></label>
      <label>Type<select value={type} onChange={(event) => changeType(event.target.value)}><option value="true_false">True/False</option><option value="multiple_choice">Multiple choice</option></select></label>
      {type === "multiple_choice" && choices.map((choice, index) => <label key={index}>Choice {String.fromCharCode(65 + index)}<input required={index < 2} value={choice} onChange={(event) => { const next = [...choices]; if (answer === choice) setAnswer(event.target.value); next[index] = event.target.value; setChoices(next); }} /></label>)}
      <label>Correct answer<select required value={answer} onChange={(event) => setAnswer(event.target.value)}>{type === "true_false" ? <><option value="True">True</option><option value="False">False</option></> : <><option value="">Select answer</option>{choices.filter((choice) => choice.trim()).map((choice, index) => <option value={choice.trim()} key={index}>{choice}</option>)}</>}</select></label>
      <div className="question-inline-editor-actions"><button type="button" className="btn btn-secondary btn-small" onClick={onCancel} disabled={busy}>Cancel</button><button type="submit" className="btn btn-primary btn-small" disabled={busy}>Save question</button></div>
    </form>
  );
}

function ManualQuestionPanel({
  courseId,
  topicId,
  groups,
  onResourcesChange,
  onCourseChange,
  onError,
  onMessage,
  lessonMaterials,
}) {
  const [uploading, setUploading] = useState(false);
  const [questionType, setQuestionType] = useState("true_false");
  const [prompt, setPrompt] = useState("");
  const [choices, setChoices] = useState(["", "", "", ""]);
  const [correctAnswer, setCorrectAnswer] = useState("True");
  const [conceptGroupId, setConceptGroupId] = useState("");
  const [saving, setSaving] = useState(false);
  const [generationMaterialId, setGenerationMaterialId] = useState("");
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    if (!generationMaterialId && lessonMaterials?.length) setGenerationMaterialId(String(lessonMaterials[0].id));
  }, [generationMaterialId, lessonMaterials]);

  async function handleGenerateQuestions() {
    if (!generationMaterialId) return;
    setGenerating(true);
    onError("");
    onMessage("Generating and classifying questions. You may continue reviewing while this runs.");
    try {
      const started = await startQuestionGeneration(generationMaterialId);
      let result;
      for (let attempt = 0; attempt < 300; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        result = await fetchQuestionGenerationTrace(started.run_id);
        if (["finished", "failed"].includes(result.run.status)) break;
      }
      if (!result || result.run.status === "running") throw new Error("Question generation is still running. Refresh this page shortly.");
      if (result.run.status === "failed") {
        const failure = [...(result.events || [])].reverse().find((event) => event.event_type === "error");
        throw new Error(failure?.message || "Question generation failed.");
      }
      onResourcesChange(await fetchLearningResources(courseId, topicId));
      onMessage("Questions generated, classified as LOTS/HOTS, and added to the approved question list.");
    } catch (err) {
      onError(err.message);
    } finally {
      setGenerating(false);
    }
  }

  function switchType(type) {
    setQuestionType(type);
    setChoices(["", "", "", ""]);
    setCorrectAnswer(type === "true_false" ? "True" : "");
  }

  async function handleUpload(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setUploading(true);
    onError("");
    onMessage("");
    try {
      const formData = new FormData();
      formData.append("pdf_file", file);
      formData.append("title", file.name.replace(/\.pdf$/i, ""));
      formData.append("outline_node_id", topicId);
      const updatedCourse = await uploadLearningMaterial(courseId, formData);
      onCourseChange(updatedCourse);
      onMessage("Question PDF uploaded and matched against the confirmed concepts.");
    } catch (err) {
      onError(err.message);
    } finally {
      setUploading(false);
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setSaving(true);
    onError("");
    onMessage("");
    try {
      const finalChoices = questionType === "true_false"
        ? ["True", "False"]
        : choices.map((choice) => choice.trim()).filter(Boolean);
      const data = await createTopicQuestion(courseId, topicId, {
        prompt: prompt.trim(),
        question_type: questionType,
        choices: finalChoices,
        correct_answer: correctAnswer,
        learning_object_group_id: conceptGroupId || null,
      });
      onResourcesChange(data.resources);
      const updatedCourse = await fetchCourse(courseId);
      onCourseChange(updatedCourse);
      setPrompt("");
      setChoices(["", "", "", ""]);
      setCorrectAnswer(questionType === "true_false" ? "True" : "");
      setConceptGroupId("");
      onMessage(
        conceptGroupId
          ? "Question added and paired with the selected concept."
          : "Question added and matched with the best available confirmed concept.",
      );
    } catch (err) {
      onError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <aside className="match-suggestion-panel panel-aside" aria-labelledby="manual-question-panel-title">
      <div className="match-suggestion-heading">
        <div>
          <span className="connection-eyebrow">Question tools</span>
          <h4 id="manual-question-panel-title">Add questions</h4>
        </div>
      </div>

      <div className="question-generation-tool">
        <strong>Generate questions</strong>
        <p>Create an editable LOTS/HOTS question set from confirmed lesson content.</p>
        <select value={generationMaterialId} disabled={generating || !lessonMaterials?.length} onChange={(event) => setGenerationMaterialId(event.target.value)}>
          {!lessonMaterials?.length && <option value="">Confirm a lesson file first</option>}
          {(lessonMaterials || []).map((material) => <option value={material.id} key={material.id}>{material.title || material.filename}</option>)}
        </select>
        <button type="button" className="btn btn-primary btn-small" disabled={generating || !generationMaterialId} onClick={handleGenerateQuestions}>{generating ? "Generating..." : "Generate questions"}</button>
      </div>

      <div className="question-source-upload">
        <p className="question-source-prompt">Did you prepare your questions in a PDF?</p>
        <label className="question-sidebar-action question-sidebar-upload">
          <span aria-hidden="true">PDF</span>
          <strong>{uploading ? "Processing question PDF..." : "Upload question PDF"}</strong>
          <input
            type="file"
            accept=".pdf,application/pdf"
            hidden
            disabled={uploading}
            onChange={handleUpload}
          />
        </label>
      </div>

      <div className="question-source-divider" role="separator">
        <span>OR</span>
      </div>

      <form className="question-manual-form" onSubmit={handleSubmit}>
        <div className="question-type-toggle" role="tablist" aria-label="Question type">
          <button
            type="button"
            role="tab"
            aria-selected={questionType === "true_false"}
            className={questionType === "true_false" ? "is-active" : ""}
            onClick={() => switchType("true_false")}
          >
            True/False
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={questionType === "multiple_choice"}
            className={questionType === "multiple_choice" ? "is-active" : ""}
            onClick={() => switchType("multiple_choice")}
          >
            Multiple choice
          </button>
        </div>

        <label className="question-manual-field">
          Question
          <textarea
            required
            rows="3"
            value={prompt}
            placeholder="Enter the question"
            onChange={(event) => setPrompt(event.target.value)}
          />
        </label>

        <label className="question-manual-field">
          Concept to tie to
          <select
            value={conceptGroupId}
            onChange={(event) => setConceptGroupId(event.target.value)}
          >
            <option value="">Best available match</option>
            {groups.map((group) => (
              <option value={group.id} key={group.id}>
                {group.label || group.learning_objects?.[0]?.title || "Untitled concept"}
              </option>
            ))}
          </select>
        </label>

        {questionType === "multiple_choice" && (
          <div className="question-manual-choice-list">
            <span>Answer choices</span>
            {choices.map((choice, index) => (
              <label key={index}>
                <span>{String.fromCharCode(65 + index)}</span>
                <input
                  required={index < 2}
                  value={choice}
                  placeholder={`Choice ${String.fromCharCode(65 + index)}`}
                  onChange={(event) => {
                    const next = [...choices];
                    next[index] = event.target.value;
                    setChoices(next);
                    if (correctAnswer === choice) setCorrectAnswer(event.target.value);
                  }}
                />
              </label>
            ))}
          </div>
        )}

        <label className="question-manual-field">
          Correct answer
          <select
            required
            value={correctAnswer}
            onChange={(event) => setCorrectAnswer(event.target.value)}
          >
            {questionType === "true_false" ? (
              <>
                <option value="True">True</option>
                <option value="False">False</option>
              </>
            ) : (
              <>
                <option value="">Select the correct answer</option>
                {choices.map((choice, index) => (
                  choice.trim() ? <option value={choice.trim()} key={index}>{String.fromCharCode(65 + index)}. {choice}</option> : null
                ))}
              </>
            )}
          </select>
        </label>

        <button type="submit" className="btn btn-primary question-manual-submit" disabled={saving}>
          {saving ? "Adding question..." : "Add question"}
        </button>
      </form>
    </aside>
  );
}

function PublishPanel({
  courseId,
  topicId,
  topic,
  groups,
  confirmedSourceCount,
  busyAction,
  onReviewStepChange,
  onResourcesChange,
  onCourseChange,
  onError,
  onMessage,
}) {
  const [publishing, setPublishing] = useState(false);
  const [deletingKey, setDeletingKey] = useState("");

  async function runDeletion(key, confirmText, successText, action) {
    if (!window.confirm(confirmText)) return;
    setDeletingKey(key);
    onError("");
    onMessage("");
    try {
      onResourcesChange(await action());
      onMessage(successText);
    } catch (err) {
      onError(err.message);
    } finally {
      setDeletingKey("");
    }
  }

  function handleDeleteObject(item) {
    const label = item.title || "this learning object";
    return runDeletion(
      `object-${item.id}`,
      `Delete "${label}"? It is removed from the course permanently.`,
      `Deleted learning object "${label}".`,
      () => deleteTopicLearningObject(courseId, topicId, item.id),
    );
  }

  function handleDeleteQuestion(question) {
    return runDeletion(
      `question-${question.id}`,
      `Delete this question? It is removed from every concept it is paired with.

${question.prompt}`,
      "Question deleted.",
      () => deleteTopicQuestion(courseId, topicId, question.id),
    );
  }

  async function handlePublish() {
    setPublishing(true);
    onError("");
    onMessage("");
    try {
      const data = await publishTopic(courseId, topicId);
      onResourcesChange(data);
      if (data.course) onCourseChange(data.course);
      const info = data.publish || {};
      const audioCount = info.audio_generated_count || 0;
      const materialCount = info.materials_processed || 0;
      onMessage(
        `Course published. ${audioCount} audio file${audioCount === 1 ? "" : "s"} generated across `
        + `${materialCount} lesson file${materialCount === 1 ? "" : "s"}.`,
      );
    } catch (err) {
      onError(err.message);
    } finally {
      setPublishing(false);
    }
  }

  return (
    <section className="connection-review-panel" aria-labelledby="publish-panel-title">
      <div className="connection-review-heading">
        <div>
          <span className="connection-eyebrow">Final review</span>
          <h3 id="publish-panel-title">Learning objects overview</h3>
          <p>
            Review every confirmed learning object for this topic, then publish to generate lesson audio.
          </p>
        </div>
        <span className="connection-source-count">
          {groups.length} concept{groups.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="review-step-indicator has-three-steps" aria-label="Review progress">
        <span className="is-complete">1</span>
        <div aria-hidden="true" />
        <span className="is-complete">2</span>
        <div aria-hidden="true" />
        <span className="is-active">3</span>
        <strong>Publish</strong>
      </div>

      {!groups.length ? (
        <div className="review-queue-empty">No confirmed learning objects are available yet.</div>
      ) : (
        <div className="connection-group-list">
          {groups.map((group, groupIndex) => {
            const isConnected = group.learning_objects.length > 1;
            const groupNumber = groupIndex + 1;
            return (
              <article className={`connection-group-card ${isConnected ? "is-connected" : ""}`} key={group.id}>
                <header>
                  <div className="connection-group-heading-copy">
                    <span className="connection-group-number" aria-label={`Concept ${groupNumber}`}>{groupNumber}</span>
                    <h4>{group.label || group.learning_objects[0]?.title || "Untitled concept"}</h4>
                  </div>
                  <span className={`connection-status ${isConnected ? "is-connected" : "is-single"}`}>
                    {isConnected
                      ? `${group.learning_objects.length} variations`
                      : "Single variation"}
                  </span>
                </header>
                <div className="publish-object-list">
                  {group.learning_objects.map((item) => (
                    <div className="publish-object-item" key={item.id}>
                      <div className="publish-item-heading">
                        <strong>{item.title}</strong>
                        <button
                          type="button"
                          className="btn btn-danger btn-small"
                          disabled={publishing || Boolean(busyAction) || Boolean(deletingKey)}
                          onClick={() => handleDeleteObject(item)}
                        >
                          {deletingKey === `object-${item.id}` ? "Deleting..." : "Delete"}
                        </button>
                      </div>
                      <FormattedLearningObjectContent
                        content={item.content}
                        className="learning-object-content-text"
                      />
                    </div>
                  ))}
                </div>
                <div className="publish-question-list">
                  <h5>
                    {group.questions?.length || 0} question
                    {(group.questions?.length || 0) === 1 ? "" : "s"}
                  </h5>
                  {!group.questions?.length ? (
                    <p className="publish-question-empty">
                      No confirmed questions are paired with this concept.
                    </p>
                  ) : (
                    group.questions.map((question) => (
                      <div className="publish-question-item" key={question.id}>
                        <div className="publish-item-heading">
                          <span aria-hidden="true">Q</span>
                          <strong>{question.prompt}</strong>
                          <button
                            type="button"
                            className="btn btn-danger btn-small"
                            disabled={publishing || Boolean(busyAction) || Boolean(deletingKey)}
                            onClick={() => handleDeleteQuestion(question)}
                          >
                            {deletingKey === `question-${question.id}` ? "Deleting..." : "Delete"}
                          </button>
                        </div>
                        {Boolean(question.choices?.length) && (
                          <ul className="publish-question-choices">
                            {question.choices.map((choice, choiceIndex) => (
                              <li
                                className={choice === question.correct_answer ? "is-correct" : ""}
                                key={`${question.id}-${choiceIndex}`}
                              >
                                {choice}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}

      <div className="review-step-actions-row">
        <button
          type="button"
          className="btn btn-secondary"
          disabled={publishing || Boolean(busyAction)}
          onClick={() => onReviewStepChange("questions")}
        >
          Back to question pairs
        </button>
        <div className="publish-status">
          {topic?.published_at && (
            <small>Last published {new Date(topic.published_at).toLocaleString()}</small>
          )}
          <button
            type="button"
            className="btn btn-primary"
            disabled={publishing || Boolean(busyAction) || confirmedSourceCount === 0}
            onClick={handlePublish}
          >
            {publishing ? "Publishing..." : topic?.published ? "Republish course" : "Publish course"}
          </button>
        </div>
      </div>
    </section>
  );
}

function LearningObjectConnections({
  courseId,
  topicId,
  topic,
  materials,
  reviewStep,
  onReviewStepChange,
  onCourseChange,
  onError,
  onMessage,
}) {
  const [resources, setResources] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState("");
  const [filter, setFilter] = useState("all");
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedIds, setSelectedIds] = useState([]);
  const [groupLabel, setGroupLabel] = useState("");

  const materialSignature = useMemo(
    () => materials
      .map((material) => `${material.id}:${Boolean(material.generated_json?.learning_objects_confirmed)}:${material.learning_objects.map((item) => `${item.id}:${item.group}:${item.title}:${(item.content || "").length}`).join(",")}`)
      .join("|"),
    [materials],
  );

  useEffect(() => {
    let cancelled = false;

    async function loadResources() {
      setLoading(true);
      const startedAt = performance.now();
      try {
        const data = await fetchLearningResources(courseId, topicId);
        console.info("[MAVIA review] Queue loaded", {
          request_ms: Math.round((performance.now() - startedAt) * 10) / 10,
          server: data.debug,
        });
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
  const confirmedSourceCount = materials.filter(
    (material) => material.generated_json?.learning_objects_confirmed
      && material.learning_objects?.length,
  ).length;
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

  useEffect(() => {
    if (reviewStep === "questions") setSelectedIds([]);
  }, [reviewStep]);

  function toggleSelection(objectId) {
    if (reviewStep !== "objects") return;
    setSelectedIds((current) => (
      current.includes(objectId)
        ? current.filter((id) => id !== objectId)
        : [...current, objectId]
    ));
  }

  async function connectSelected() {
    if (reviewStep !== "objects" || selectedIds.length < 2) return;
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
    if (reviewStep !== "objects") return;
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
    const startedAt = performance.now();
    const evidence = suggestion.evidence || {};
    console.groupCollapsed(
      `[MAVIA review] ${decision} object pair ${suggestion.id}`,
    );
    console.info("Request", {
      decision,
      suggestion_id: suggestion.id,
      source: suggestion.source_learning_object?.title,
      candidate: suggestion.candidate_learning_object?.title,
      similarity: suggestion.similarity_score,
      confidence: suggestion.confidence,
      winner_margin: evidence.winner_margin,
      minimum_group_member_score: evidence.minimum_group_member_score,
      content_support: evidence.content_support,
      minimum_group_content_support: evidence.minimum_group_content_support,
    });
    try {
      const action = decision === "accept"
        ? acceptLearningObjectMatchSuggestion
        : rejectLearningObjectMatchSuggestion;
      const data = await action(courseId, topicId, suggestion.id);
      console.info("Response", {
        request_ms: Math.round((performance.now() - startedAt) * 10) / 10,
        server: data.debug,
        remaining_object_reviews: data.match_suggestions?.length || 0,
      });
      setResources(data);
      onMessage(
        decision === "accept"
          ? "Suggested learning objects were connected."
          : "Suggested connection was rejected and the objects remain separate.",
      );
    } catch (err) {
      console.error("Review failed", {
        request_ms: Math.round((performance.now() - startedAt) * 10) / 10,
        error: err,
      });
      onError(err.message);
    } finally {
      console.groupEnd();
      setBusyAction("");
    }
  }

  async function reviewQuestion(question, decision, learningObjectGroupId = null) {
    setBusyAction(`question-${decision}-${question.id}`);
    onError("");
    onMessage("");
    const startedAt = performance.now();
    console.groupCollapsed(`[MAVIA review] ${decision} question ${question.id}`);
    console.info("Request", {
      decision,
      question_id: question.id,
      learning_object_group_id: learningObjectGroupId,
      current_pairing: question.learning_object_links?.[0],
    });
    try {
      const data = await reviewQuestionPairing(
        courseId,
        topicId,
        question.id,
        decision,
        learningObjectGroupId,
      );
      console.info("Response", {
        request_ms: Math.round((performance.now() - startedAt) * 10) / 10,
        server: data.debug,
        remaining_question_reviews: (data.question_pairings || []).filter((item) => {
          const status = item.learning_object_links?.[0]?.review_status;
          return !status || status === "pending_review" || status === "unmatched";
        }).length,
      });
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
      console.error("Review failed", {
        request_ms: Math.round((performance.now() - startedAt) * 10) / 10,
        error: err,
      });
      onError(err.message);
      return false;
    } finally {
      console.groupEnd();
      setBusyAction("");
    }
  }

  async function editQuestion(question, values) {
    setBusyAction(`question-edit-${question.id}`);
    onError("");
    onMessage("");
    try {
      const data = await updateTopicQuestion(courseId, topicId, question.id, values);
      setResources(data.resources);
      onMessage("Question updated and LOTS/HOTS classification refreshed.");
      return true;
    } catch (err) {
      onError(err.message);
      return false;
    } finally {
      setBusyAction("");
    }
  }

  return (
    <div className={`connection-review-layout ${reviewStep === "publish" ? "" : "has-recommendations"}`.trim()}>
      {reviewStep === "objects" && (
      <section
        className="connection-review-panel"
        aria-labelledby="connection-review-title"
      >
      <div className="connection-review-heading">
        <div>
          <span className="connection-eyebrow">Teacher review</span>
          <h3 id="connection-review-title">Related Concepts</h3>
          <p>
            Review learning objects from every PDF and connect equivalent content into one concept group.
            Each object remains a separate variation for the learning-path module.
          </p>
        </div>
        <span className="connection-source-count">
          {confirmedSourceCount} confirmed source{confirmedSourceCount === 1 ? "" : "s"}
        </span>
      </div>

      {loading ? (
        <div className="connection-empty">Checking learning-object connections…</div>
      ) : (
        <>
          <div className="connection-review-main">
          <div className="connection-toolbar">
            <div className="connection-filters" aria-label="Filter learning-object groups">
              {[
                ["all", "All", groups.length],
                ["single", "Standalone", singletonGroups.length],
                ["connected", "Grouped", connectedGroups.length],
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
              {confirmedSourceCount === 0
                ? "Confirm the extracted learning objects in a lesson file before reviewing connections."
                : searchTerm.trim()
                  ? "No concepts match your search."
                  : filter === "connected"
                    ? "No grouped concepts match this view. Open “Standalone” and connect equivalent objects."
                    : "No learning-object groups match this filter."}
            </div>
          ) : (
            <div className="connection-group-list">
              {visibleGroups.map((group, groupIndex) => {
                const isConnected = group.learning_objects.length > 1;
                const groupNumber = groupIndex + 1;
                return (
                  <article className={`connection-group-card ${isConnected ? "is-connected" : ""}`} key={group.id}>
                    <header>
                      <div className="connection-group-heading-copy">
                        <span className="connection-group-number" aria-label={`Concept ${groupNumber}`}>{groupNumber}</span>
                        <h4>{group.label || group.learning_objects[0]?.title || "Untitled concept"}</h4>
                      </div>
                      <span className={`connection-status ${isConnected ? "is-connected" : "is-single"}`}>
                        {isConnected
                          ? `${group.learning_objects.length} variations`
                          : "Single variation"}
                      </span>
                    </header>

                    <div className="connection-object-list">
                      {group.learning_objects.map((item, itemIndex) => {
                        const material = materialById.get(Number(item.material));
                        const isImage = isImageLearningObject(item);
                        const isMissingImageDescription = isImage && !item.content?.trim();
                        return (
                          <div className="connection-object-row" key={item.id}>
                            {reviewStep === "objects" && (
                              <label className="connection-object-select">
                                <input
                                  type="checkbox"
                                  checked={selectedIds.includes(item.id)}
                                  disabled={Boolean(busyAction)}
                                  onChange={() => toggleSelection(item.id)}
                                />
                                <span className="sr-only">Select {item.title}</span>
                              </label>
                            )}
                            <div className="connection-object-copy">
                              <div className="connection-object-title-row">
                                <span className="connection-object-order">{groupNumber}.{itemIndex + 1}</span>
                                <strong>{item.title}</strong>
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
                                      : "Suggested description · review recommended."}
                                  </span>
                                </div>
                              )}
                              <FormattedLearningObjectContent
                                content={item.content}
                                className={`learning-object-content-text connection-object-full-content ${
                                  isImage ? "image-description-text" : ""
                                }`.trim()}
                              />
                              <small>
                                {item.kind === "image" ? "Image learning object" : "Text learning object"}
                                {item.section_title ? ` · Section: ${item.section_title}` : ""}
                              </small>
                              <div className="connection-object-source-footer">
                                <strong>Source material:</strong>{" "}
                                <span>{material?.filename || material?.title || `PDF ${item.material}`}</span>
                              </div>
                            </div>
                            {isConnected && reviewStep === "objects" && (
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
                        <div className="connection-question-heading">
                          <span aria-hidden="true">?</span>
                          <div>
                            <h5>Questions paired with this concept</h5>
                            <small>{group.questions.length} linked question{group.questions.length === 1 ? "" : "s"}</small>
                          </div>
                        </div>
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

      {!loading && reviewStep === "objects" && selectedIds.length > 0 && (
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
            Concept label <span>(optional)</span>
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
      )}
      {reviewStep === "objects" && (
        <ObjectPairsPanel
          suggestions={matchSuggestions}
          materialById={materialById}
          busyAction={busyAction}
          pendingQuestionCount={questionReviewQueue.length}
          onReviewStepChange={onReviewStepChange}
          onReview={reviewMatchSuggestion}
        />
      )}
      {reviewStep === "questions" && (
        <>
          <ReviewQueuePanel
            questionPairings={allQuestionPairings}
            groups={groups}
            materialById={materialById}
            busyAction={busyAction}
            onReviewStepChange={onReviewStepChange}
            onReviewQuestion={reviewQuestion}
            onEditQuestion={editQuestion}
          />
          <ManualQuestionPanel
            courseId={courseId}
            topicId={topicId}
            groups={groups}
            onResourcesChange={setResources}
            onCourseChange={onCourseChange}
            onError={onError}
            onMessage={onMessage}
            lessonMaterials={materials.filter((material) => material.generated_json?.learning_objects_confirmed && material.learning_objects?.length)}
          />
        </>
      )}
      {reviewStep === "publish" && (
        <PublishPanel
          courseId={courseId}
          topicId={topicId}
          topic={topic}
          groups={groups}
          confirmedSourceCount={confirmedSourceCount}
          busyAction={busyAction}
          onReviewStepChange={onReviewStepChange}
          onResourcesChange={setResources}
          onCourseChange={onCourseChange}
          onError={onError}
          onMessage={onMessage}
        />
      )}
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

function MaterialCard({ material, courseId, onCourseChange, onError, onMessage, onReviewConnections }) {
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
  const isAssessmentDocument = isQuestionMaterial(material);
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
      onMessage(isAssessmentDocument ? "Question document deleted." : "Lesson material deleted.");
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

  if (isAssessmentDocument) {
    return (
      <article className="material-card question-document-card">
        <div className="material-header">
          <div>
            <span className="connection-eyebrow">Question file</span>
            <h4>{material.title}</h4>
            <p>{material.filename} · {material.questions?.length || 0} extracted questions</p>
          </div>
          <span className={`status-pill status-${material.status}`}>{material.status}</span>
        </div>
        <div className="assessment-document-banner">
          <strong>Question document detected</strong>
          <span>Questions are matched against learning objects from lesson PDFs in this topic.</span>
        </div>
        <div className="question-document-actions">
          <button type="button" className="btn btn-primary btn-small" onClick={onReviewConnections}>
            Review question assignments
          </button>
          <button
            type="button"
            className="btn btn-danger btn-small"
            disabled={Boolean(busyAction)}
            onClick={() => {
              if (window.confirm("Delete this question document and all extracted questions?")) removeMaterial();
            }}
          >
            {busyAction === "delete-material" ? "Deleting..." : "Delete question file"}
          </button>
        </div>
        <div className="question-document-list">
          {(material.questions || []).map((question, index) => {
            const link = question.learning_object_links?.[0];
            const isConfirmed = ["auto_confirmed", "teacher_confirmed"].includes(link?.review_status);
            const statusLabel = isConfirmed
              ? "Assigned"
              : link?.review_status === "pending_review"
                ? "Needs review"
                : "Not matched";
            return (
              <div className="question-document-row" key={question.id}>
                <span>{index + 1}</span>
                <div>
                  <strong>{question.prompt}</strong>
                  <small>
                    {statusLabel}
                    {link?.learning_object_title ? ` · ${link.learning_object_title}` : ""}
                  </small>
                </div>
              </div>
            );
          })}
        </div>
      </article>
    );
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
              <div className={`learning-object-layout ${canEditLearningObjects ? "has-side-panel" : ""}`.trim()}>
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
                                    : "Suggested description · review recommended."}
                                </span>
                              </div>
                            )}
                            <FormattedLearningObjectContent
                              content={item.content}
                              className={`learning-object-content-text ${
                                isImage ? "image-description-text" : ""
                              }`.trim()}
                            />
                          </>
                        )}
                      </div>
                    </Fragment>
                  );
                })}
              </div>

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
  const [searchParams] = useSearchParams();
  const uploadedMaterialId = searchParams.get("material");
  const [course, setCourse] = useState(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [uploading, setUploading] = useState(false);
  const [activeSource, setActiveSource] = useState(uploadedMaterialId || "connections");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [connectionReviewStep, setConnectionReviewStep] = useState("objects");

  useEffect(() => {
    setActiveSource(uploadedMaterialId || "connections");
  }, [courseId, topicId, uploadedMaterialId]);

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
  const lessonMaterials = useMemo(
    () => materials.filter((material) => !isQuestionMaterial(material)),
    [materials],
  );
  const confirmedLessonMaterials = useMemo(
    () => lessonMaterials.filter(
      (material) => Boolean(material.generated_json?.learning_objects_confirmed),
    ),
    [lessonMaterials],
  );
  const pendingLessonMaterials = useMemo(
    () => lessonMaterials.filter(
      (material) => !material.generated_json?.learning_objects_confirmed,
    ),
    [lessonMaterials],
  );
  const questionMaterials = useMemo(
    () => materials.filter((material) => isQuestionMaterial(material)),
    [materials],
  );
  const selectedMaterial = materials.find(
    (material) => String(material.id) === String(activeSource),
  );

  useEffect(() => {
    if (!course || String(course.id) !== String(courseId)) return;
    if (!materials.length) {
      setActiveSource("connections");
      return;
    }
    setActiveSource((current) => {
      if (current === "connections") return current;
      if (materials.some((material) => String(material.id) === String(current))) {
        return current;
      }
      return "connections";
    });
  }, [materials, course, courseId]);

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
      const uploadedMaterial = uploadedMaterialFromResponse(updatedCourse);
      if (uploadedMaterial) {
        setActiveSource(String(uploadedMaterial.id));
      }
      if (uploadedMaterial?.status === "failed") {
        setError(uploadedMaterial.error_message || "Content extraction failed for this PDF.");
        return;
      }
      const objectCount = uploadedMaterial?.learning_objects?.length || 0;
      const questionCount = uploadedMaterial?.questions?.length || 0;
      const isQuestionDocument = isQuestionMaterial(uploadedMaterial);
      setMessage(
        isQuestionDocument
          ? `Question document detected: ${questionCount} question${questionCount === 1 ? "" : "s"} extracted and excluded from lesson narration.`
          : objectCount
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
      <aside className="card lesson-pdf-sidebar" aria-label="Uploaded PDF navigation">
        <div className="lesson-sidebar-brand-row">
          <Link to="/courses" className="lesson-sidebar-brand" title="Mavia home">
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
          <nav className="lesson-pdf-navigation" aria-label="Uploaded PDFs">
            <button
              type="button"
              className={`lesson-pdf-nav-item connection-nav-item ${activeSource === "connections" ? "is-active" : ""}`}
              aria-current={activeSource === "connections" ? "page" : undefined}
              title={sidebarCollapsed ? "Review connections" : undefined}
              onClick={() => setActiveSource("connections")}
            >
              <span className="lesson-pdf-short-label" aria-hidden="true">R</span>
              <span className="lesson-pdf-nav-copy">
                <strong>Review Connections</strong>
              </span>
            </button>

            {confirmedLessonMaterials.length > 0 && (
              <>
                <div className="lesson-pdf-nav-label confirmed-file-nav-label">
                  Confirmed lesson files
                  <span>{confirmedLessonMaterials.length}</span>
                </div>
                <ul className="lesson-pdf-list">
                  {confirmedLessonMaterials.map((material, index) => {
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
                            <small>Ready for connections</small>
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </>
            )}

            {pendingLessonMaterials.length > 0 && (
              <>
                <div className="lesson-pdf-nav-label pending-file-nav-label">
                  Awaiting confirmation
                  <span>{pendingLessonMaterials.length}</span>
                </div>
                <ul className="lesson-pdf-list">
                  {pendingLessonMaterials.map((material, index) => {
                    const isActive = String(activeSource) === String(material.id);
                    return (
                      <li key={material.id}>
                        <button
                          type="button"
                          className={`lesson-pdf-nav-item pending-file-nav-item ${isActive ? "is-active" : ""}`}
                          aria-current={isActive ? "page" : undefined}
                          title={material.filename || material.title}
                          onClick={() => setActiveSource(String(material.id))}
                        >
                          <span className="lesson-pdf-short-label" aria-hidden="true">P{index + 1}</span>
                          <span className="lesson-pdf-nav-copy">
                            <strong>{material.title || `Lesson PDF ${index + 1}`}</strong>
                            <small>Review and confirm first</small>
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </>
            )}

            {questionMaterials.length > 0 && (
              <>
                <div className="lesson-pdf-nav-label question-file-nav-label">
                  Question files
                  <span>{questionMaterials.length}</span>
                </div>
                <ul className="lesson-pdf-list">
                  {questionMaterials.map((material, index) => {
                const isActive = String(activeSource) === String(material.id);
                return (
                  <li key={material.id}>
                    <button
                      type="button"
                      className={`lesson-pdf-nav-item question-file-nav-item ${isActive ? "is-active" : ""}`}
                      aria-current={isActive ? "page" : undefined}
                      title={material.filename || material.title}
                      onClick={() => setActiveSource(String(material.id))}
                    >
                      <span className="lesson-pdf-short-label" aria-hidden="true">Q{index + 1}</span>
                      <span className="lesson-pdf-nav-copy">
                        <strong>{material.title || `Question PDF ${index + 1}`}</strong>
                        <small>Questions only</small>
                      </span>
                    </button>
                  </li>
                );
                  })}
                </ul>
              </>
            )}
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
            {!(activeSource === "connections" && connectionReviewStep === "questions") && (
              <label className="btn btn-primary">
                {uploading ? "Processing..." : "Upload PDF"}
                <input
                  type="file"
                  accept=".pdf,application/pdf"
                  hidden
                  disabled={uploading}
                  onChange={handleMaterialUpload}
                />
              </label>
            )}
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
            topic={topic}
            materials={materials}
            reviewStep={connectionReviewStep}
            onReviewStepChange={setConnectionReviewStep}
            onCourseChange={setCourse}
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
              onReviewConnections={() => setActiveSource("connections")}
            />
          </div>
        ) : (
          <div className="empty-state">Choose a PDF from the sidebar.</div>
        )}
      </main>
    </section>
  );
}
