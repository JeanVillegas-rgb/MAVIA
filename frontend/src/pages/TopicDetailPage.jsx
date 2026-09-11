import { Fragment, useEffect, useMemo, useRef, useState } from "react";
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
  generateAllObjectVersions,
  fetchGenerationRunEvents,
  publishTopic,
  regenerateImageNarrations,
  rejectLearningObjectMatchSuggestion,
  assignVersionSlot,
  editVersionText,
  generateObjectVersions,
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
      <div className="review-step-indicator has-four-steps" aria-label="Review progress">
        <span className="is-active">1</span>
        <div aria-hidden="true" />
        <span>2</span>
        <div aria-hidden="true" />
        <span>3</span>
        <div aria-hidden="true" />
        <span>4</span>
        <strong>Object pairs</strong>
      </div>

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
                        <img className="review-source-image" src={source.image_url} alt={source.title || "Source A"} />
                      )}
                      <p className="match-source-content">{source.content || "No narration content."}</p>
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
                        <img className="review-source-image" src={candidate.image_url} alt={candidate.title || "Source B"} />
                      )}
                      <p className="match-source-content">{candidate.content || "No narration content."}</p>
                    </div>
                  </div>
                  <div className="match-suggestion-actions">
                    <button type="button" className="btn btn-secondary btn-small" disabled={Boolean(busyAction)} onClick={() => onReview(suggestion, "reject")}>
                      {busyAction === `suggestion-reject-${suggestion.id}` ? "Declining..." : "Decline"}
                    </button>
                    <button type="button" className="btn btn-primary btn-small" disabled={Boolean(busyAction)} onClick={() => onReview(suggestion, "accept")}>
                      {busyAction === `suggestion-accept-${suggestion.id}` ? "Accepting..." : "Accept"}
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        </>
      )}
      <div className="review-step-actions review-step-actions-next">
        <button type="button" className="btn btn-primary" disabled={Boolean(busyAction)} onClick={() => onReviewStepChange("versions")}>
          Next step: Content versions
        </button>
      </div>
    </aside>
  );
}


// A question leaves the review queue as soon as the teacher has ruled on it.
// Declining sets `teacher_unpaired`, which is a decision -- not an unreviewed
// state -- so it must not keep the question in the queue. `auto_confirmed`
// never needed a teacher at all. Exported so the classification can be tested
// without rendering the panel.
export const PENDING_REVIEW_STATUSES = ["pending_review", "unmatched"];

export function questionReviewState(question) {
  const link = question?.learning_object_links?.[0];
  const status = link?.review_status || "unmatched";
  const isPending = PENDING_REVIEW_STATUSES.includes(status);
  let label = "Needs review";
  if (status === "auto_confirmed") {
    label = "Auto-paired";
  } else if (status === "teacher_confirmed") {
    label = link?.method === "teacher_selected" ? "Moved" : "Paired";
  } else if (status === "teacher_unpaired") {
    label = "Unpaired";
  } else if (status === "unmatched") {
    label = "No confident match";
  }
  return { status, isPending, label };
}

function ReviewQueuePanel({
  questionPairings,
  groups,
  materialById,
  busyAction,
  onReviewStepChange,
  generationProps,
}) {
  return (
    <section className="connection-review-panel" aria-labelledby="match-suggestion-title">
      <div className="connection-review-heading">
        <div>
          <span className="connection-eyebrow">Question generation</span>
          <h3 id="match-suggestion-title">Generate and review questions</h3>
          <p>Generate questions from confirmed lesson content, then check and edit the saved question list.</p>
        </div>
        <span className="connection-source-count">{questionPairings.length} question{questionPairings.length === 1 ? "" : "s"}</span>
      </div>
      <div className="review-step-indicator has-four-steps" aria-label="Review progress">
        <span className="is-complete">1</span>
        <div aria-hidden="true" />
        <span className="is-complete">2</span>
        <div aria-hidden="true" />
        <span className="is-active">3</span>
        <div aria-hidden="true" />
        <span>4</span>
        <strong>Question pairs</strong>
      </div>

      <QuestionGenerationTool
        {...generationProps}
        groups={groups}
        materialById={materialById}
      />

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

function QuestionEditForm({ question, groups, busy, onCancel, onSave }) {
  const [prompt, setPrompt] = useState(question.prompt || "");
  const [type, setType] = useState(
    question.question_type === "open_ended" ? "multiple_choice" : question.question_type,
  );
  const [choices, setChoices] = useState(() => [...(question.choices || []), "", "", "", ""].slice(0, 4));
  const [answer, setAnswer] = useState(question.correct_answer || "");
  const [conceptGroupId, setConceptGroupId] = useState(() => {
    const link = question.learning_object_links?.[0];
    return link?.learning_object_group_id ? String(link.learning_object_group_id) : "";
  });

  function changeType(next) {
    setType(next);
    if (next === "true_false") {
      setChoices(["True", "False", "", ""]);
      setAnswer(["true", "false"].includes(answer.toLowerCase()) ? answer : "True");
    }
  }

  return (
    <form className="question-inline-editor" onSubmit={(event) => {
      event.preventDefault();
      onSave({
        prompt: prompt.trim(),
        question_type: type,
        choices: type === "true_false" ? ["True", "False"] : choices.filter((choice) => choice.trim()),
        correct_answer: answer,
        learning_object_group_id: conceptGroupId || null,
      });
    }}>
      <label>Question<textarea required rows="3" value={prompt} onChange={(event) => setPrompt(event.target.value)} /></label>
      <label>Type<select value={type} onChange={(event) => changeType(event.target.value)}><option value="true_false">True/False</option><option value="multiple_choice">Multiple choice</option></select></label>
      <label>Concept to tie to<select value={conceptGroupId} onChange={(event) => setConceptGroupId(event.target.value)}>
        <option value="">No concept assigned</option>
        {groups.map((group) => <option value={group.id} key={group.id}>{group.label || group.learning_objects?.[0]?.title || "Untitled concept"}</option>)}
      </select></label>
      {type === "multiple_choice" && choices.map((choice, index) => (
        <label key={index}>Choice {String.fromCharCode(65 + index)}<input required={index < 2} value={choice} onChange={(event) => {
          const next = [...choices];
          if (answer === choice) setAnswer(event.target.value);
          next[index] = event.target.value;
          setChoices(next);
        }} /></label>
      ))}
      <label>Correct answer<select required value={answer} onChange={(event) => setAnswer(event.target.value)}>
        {type === "true_false" ? <><option value="True">True</option><option value="False">False</option></> : <><option value="">Select answer</option>{choices.filter((choice) => choice.trim()).map((choice, index) => <option value={choice.trim()} key={index}>{choice}</option>)}</>}
      </select></label>
      <div className="question-inline-editor-actions"><button type="button" className="btn btn-secondary btn-small" onClick={onCancel} disabled={busy}>Back</button><button type="submit" className="btn btn-primary btn-small" disabled={busy}>{busy ? "Saving..." : "Save"}</button></div>
    </form>
  );
}

// Where a version's wording came from, in the teacher's words rather than the
// database's. Exported so the labelling can be checked without rendering.
export function versionOriginLabel(entry, materialTitle) {
  if (!entry) return "";
  const source = entry.origin === "source_pdf"
    ? `From ${materialTitle || "another PDF"}`
    : "AI generated";
  if (entry.assigned_by === "teacher") return `${source} · Teacher confirmed`;
  if (entry.assigned_by === "llm_validated") return `${source} · AI classified`;
  return source;
}

function VersionSlotCard({
  slotKey,
  heading,
  entry,
  text,
  originLabel,
  readOnly,
  busy,
  isEditing,
  onBeginEdit,
  onCancelEdit,
  onSave,
  onGenerate,
  actions,
}) {
  const [draft, setDraft] = useState(text || "");
  useEffect(() => { setDraft(text || ""); }, [text, isEditing]);

  return (
    <article className={`version-slot is-${slotKey}`}>
      <header className="version-slot-head">
        <span className={`version-slot-label is-${slotKey}`}>{heading}</span>
        {originLabel && <small className="version-slot-origin">{originLabel}</small>}
        {actions && <div className="version-slot-role-top">{actions}</div>}
      </header>

      {text ? (
        isEditing ? (
          <div className="version-slot-editor">
            <textarea
              value={draft}
              rows={5}
              onChange={(event) => setDraft(event.target.value)}
              aria-label={`${heading} text`}
            />
            <div className="version-slot-actions">
              <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={onCancelEdit}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary btn-small"
                disabled={busy || !draft.trim()}
                onClick={() => onSave(draft.trim())}
              >
                {busy ? "Saving..." : "Save"}
              </button>
            </div>
          </div>
        ) : (
          <>
            <p className="version-slot-text">{text}</p>
            {!readOnly && (
              <div className="version-slot-actions">
                <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={onBeginEdit}>
                  Edit wording
                </button>
              </div>
            )}
          </>
        )
      ) : (
        <div className="version-slot-empty">
          <p>Not written yet.</p>
          <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={onGenerate}>
            {busy ? "Generating, this takes a few minutes..." : "Generate"}
          </button>
        </div>
      )}
    </article>
  );
}

function VersionRoleSelect({ sourceId, currentSlot, busy, onAssign }) {
  const roles = ["NORMAL", "SIMPLIFIED", "ELABORATED", "EXTRA"]
    .filter((slot) => slot !== currentSlot);
  return (
    <label className="version-role-select">
      <span>Change role</span>
      <select
        value=""
        disabled={busy}
        onChange={(event) => {
          if (event.target.value) onAssign(sourceId, event.target.value);
        }}
      >
        <option value="">Select a role…</option>
        {roles.map((slot) => (
          <option value={slot} key={slot}>
            {slot === "NORMAL" ? "Make Normal"
              : slot === "EXTRA" ? "Keep as Extra"
              : `Move to ${slot === "SIMPLIFIED" ? "Simplified" : "Elaborated"}`}
          </option>
        ))}
      </select>
    </label>
  );
}

function VersionReviewPanel({
  groups,
  materialById,
  busyAction,
  onReviewStepChange,
  onAssignSlot,
  onGenerate,
  onGenerateAll,
  generationEvents,
  onEditVersion,
}) {
  const [chunkIndex, setChunkIndex] = useState(0);
  const [editingSlot, setEditingSlot] = useState(null);

  const chunks = useMemo(
    () => groups.filter((group) => group.versions?.representative_id),
    [groups],
  );

  useEffect(() => {
    setChunkIndex((current) => Math.max(0, Math.min(current, chunks.length - 1)));
  }, [chunks.length]);

  const chunk = chunks[chunkIndex];
  useEffect(() => { setEditingSlot(null); }, [chunk?.id]);

  const versions = chunk?.versions;
  const extraCount = chunks.reduce(
    (count, item) => count + (item.versions?.extras?.length || 0),
    0,
  );
  const pdfVariantCount = chunks.reduce(
    (count, item) => count + (item.learning_objects?.length || 0),
    0,
  );
  const classificationComplete = chunks.every(
    (item) => item.versions?.classification_complete !== false,
  );
  const conceptsToClassify = chunks.filter(
    (item) => item.versions?.classification_complete === false,
  ).length;
  const missingSlotCount = chunks.reduce((count, item) => {
    if (item.versions?.classification_complete === false) return count;
    const slots = item.versions?.slots || {};
    return count + (slots.simplified ? 0 : 1) + (slots.elaborated ? 0 : 1);
  }, 0);
  const latestGenerationEvent = generationEvents[generationEvents.length - 1];
  const generationProgress = [...generationEvents].reverse().find((event) => event.data?.total);
  const representative = chunk?.learning_objects?.find(
    (item) => Number(item.id) === Number(versions?.representative_id),
  );
  const originalMaterial = materialById.get(Number(representative?.material));

  function materialTitleFor(entry) {
    if (!entry?.source_learning_object_id) return null;
    const source = chunk?.learning_objects?.find(
      (item) => Number(item.id) === Number(entry.source_learning_object_id),
    );
    const material = materialById.get(Number(source?.material));
    return material?.filename || material?.title || null;
  }

  return (
    <section className="connection-review-panel" aria-labelledby="version-review-title">
      <div className="connection-review-heading">
        <div>
          <span className="connection-eyebrow">Review queue</span>
          <h3 id="version-review-title">Review content versions</h3>
          <p>
            Gemma automatically assigns every existing PDF variant to Normal, Simplified, Elaborated,
            or Extra. You can change a source's role, then generate missing versions individually or all
            at once.
          </p>
        </div>
        <div className="version-review-heading-actions">
          <span className="connection-source-count">
            {chunks.length} concepts · {pdfVariantCount} PDF variants · {extraCount} extra{extraCount === 1 ? "" : "s"}
          </span>
          {!classificationComplete ? (
            <span className="connection-source-count">
              {busyAction === "version-generate-all"
                ? "Classifying automatically…"
                : `${conceptsToClassify} awaiting classification`}
            </span>
          ) : (
            <button
              type="button"
              className="btn btn-primary btn-small"
              disabled={Boolean(busyAction) || missingSlotCount === 0}
              onClick={onGenerateAll}
              title="Generate every missing Simplified and Elaborated version."
            >
              {busyAction === "version-generate-missing-all"
                ? "Generating missing…"
                : missingSlotCount === 0
                  ? "All versions complete"
                  : `Generate all missing (${missingSlotCount})`}
            </button>
          )}
        </div>
      </div>
      {(busyAction === "version-generate-all" || generationEvents.length > 0) && (
        <div className="version-generation-progress" role="status" aria-live="polite">
          <strong>{busyAction === "version-generate-all" ? "Classifying existing PDF variants" : "Classification finished"}</strong>
          {generationProgress?.data?.index && (
            <span>{generationProgress.data.index} of {generationProgress.data.total} concepts</span>
          )}
          {latestGenerationEvent && <p>{latestGenerationEvent.message}</p>}
        </div>
      )}
      <div className="review-step-indicator has-four-steps" aria-label="Review progress">
        <span className="is-complete">1</span>
        <div aria-hidden="true" />
        <span className="is-active">2</span>
        <div aria-hidden="true" />
        <span>3</span>
        <div aria-hidden="true" />
        <span>4</span>
        <strong>Content versions</strong>
      </div>

      {!chunks.length ? (
        <div className="review-queue-empty">No concepts to review yet.</div>
      ) : (
        <>
          <ReviewQueueNavigator
            index={chunkIndex}
            count={chunks.length}
            onChange={setChunkIndex}
            disabled={Boolean(busyAction)}
            itemLabel="Concept"
          />

          <h4 className="version-chunk-title">{representative?.title || "Untitled concept"}</h4>
          <p className="muted-text">
            {chunk.learning_objects.length} grouped PDF variant{chunk.learning_objects.length === 1 ? "" : "s"}
            {versions?.classification_complete === false
              ? " awaiting Gemma classification"
              : " classified into Normal, Simplified, Elaborated, or Extra"}.
          </p>

          {versions?.classification_complete === false ? (
            <div className="review-queue-empty">
              <strong>Existing PDF variants — not generated:</strong>
              <div className="version-slot-grid">
                {chunk.learning_objects.map((item, index) => {
                  const material = materialById.get(Number(item.material));
                  return (
                    <VersionSlotCard
                      key={item.id}
                      slotKey="extra"
                      heading={`PDF variant ${index + 1} · Unclassified`}
                      text={item.content}
                      originLabel={`From ${material?.filename || material?.title || `PDF ${item.material}`}`}
                      readOnly
                      busy={false}
                    />
                  );
                })}
              </div>
              <p>
                Gemma is assigning these existing texts to roles automatically. Afterward, each missing
                Simplified or Elaborated role will have its own Generate button.
              </p>
            </div>
          ) : (versions?.needs_confirmation || []).map((pending) => {
            const candidate = chunk.learning_objects.find(
              (item) => Number(item.id) === Number(pending.learning_object_id),
            );
            if (!candidate) return null;
            return (
              <div className="version-pending-decision" key={pending.learning_object_id}>
                <p>
                  This source version needs your decision. Choose how to use it below.
                </p>
                {pending.llm_slot && (
                  <small className="muted-text">
                    AI suggested {pending.llm_slot.toLowerCase()}
                    {pending.llm_confidence != null ? ` (${Math.round(pending.llm_confidence * 100)}% confidence)` : ""};
                    readability checks suggested {pending.readability_slot?.toLowerCase() || "no clear role"}
                    {pending.readability_confident ? "." : " but did not clear the automatic threshold."}
                  </small>
                )}
                <blockquote>{candidate.content}</blockquote>
                <div className="version-slot-actions">
                  {["SIMPLIFIED", "ELABORATED", "EXTRA"].map((slot) => (
                    <button
                      type="button"
                      key={slot}
                      className={slot === "EXTRA" ? "btn btn-secondary btn-small" : "btn btn-primary btn-small"}
                      disabled={Boolean(busyAction)}
                      onClick={() => onAssignSlot(pending.learning_object_id, slot)}
                    >
                      {slot === "SIMPLIFIED" ? "Use as Simplified"
                        : slot === "ELABORATED" ? "Use as Elaborated"
                        : "Keep as extra"}
                    </button>
                  ))}
                </div>
              </div>
            );
          })}

          {versions?.classification_complete !== false && <div className="version-slot-grid">
            <VersionSlotCard
              slotKey="original"
              heading={versions?.original_selected ? "Normal" : "Normal candidate"}
              text={representative?.content}
              originLabel={versions?.original_selected
                ? `Selected from ${originalMaterial?.filename || originalMaterial?.title || "this PDF"}`
                : "The Normal version will be selected during source classification"}
              readOnly
              busy={false}
            />
            {["simplified", "elaborated"].map((slotKey) => {
              const entry = versions?.slots?.[slotKey];
              const busyKey = entry
                ? `version-edit-${entry.id}`
                : `version-generate-${versions?.representative_id}-${slotKey}`;
              return (
                <VersionSlotCard
                  key={slotKey}
                  slotKey={slotKey}
                  heading={slotKey === "simplified" ? "Simplified" : "Elaborated"}
                  entry={entry}
                  text={entry?.text}
                  originLabel={versionOriginLabel(entry, materialTitleFor(entry))}
                  busy={busyAction === busyKey}
                  isEditing={editingSlot === slotKey}
                  onBeginEdit={() => setEditingSlot(slotKey)}
                  onCancelEdit={() => setEditingSlot(null)}
                  onSave={async (narration) => {
                    const done = await onEditVersion(entry.id, narration);
                    if (done) setEditingSlot(null);
                  }}
                  onGenerate={() => onGenerate(versions.representative_id, slotKey.toUpperCase())}
                  actions={entry?.source_learning_object_id ? (
                    <VersionRoleSelect
                      sourceId={entry.source_learning_object_id}
                      currentSlot={slotKey.toUpperCase()}
                      busy={Boolean(busyAction)}
                      onAssign={onAssignSlot}
                    />
                  ) : null}
                />
              );
            })}
          </div>}

          {versions?.classification_complete !== false && (versions?.extras || []).length > 0 && (
            <details className="version-extra-block">
              <summary>Other source versions ({versions.extras.length})</summary>
              <p>
                Preserved as alternatives. Select a source below to replace a main version.
              </p>
              {versions.extras.map((entry) => (
                <div key={entry.id}><VersionSlotCard
                  key={entry.id}
                  slotKey="extra"
                  heading="Extra"
                  entry={entry}
                  text={entry.text}
                  originLabel={versionOriginLabel(entry, materialTitleFor(entry))}
                  readOnly
                  busy={false}
                />
                  {entry.source_learning_object_id && (
                    <VersionRoleSelect
                      sourceId={entry.source_learning_object_id}
                      currentSlot="EXTRA"
                      busy={Boolean(busyAction)}
                      onAssign={onAssignSlot}
                    />
                  )}
                </div>
              ))}
            </details>
          )}
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
          onClick={() => onReviewStepChange("questions")}
        >
          Next step: Question pairs
        </button>
      </div>
    </section>
  );
}

function QuestionGenerationTool({
  courseId,
  topicId,
  lessonMaterials,
  groups,
  materialById,
  onResourcesChange,
  onError,
  onMessage,
}) {
  const [generatingKey, setGeneratingKey] = useState("");
  const [generationProgress, setGenerationProgress] = useState("");
  const learningObjects = useMemo(
    () => (groups || []).flatMap((group) => {
      const representative = (group.learning_objects || []).find(
        (item) => Number(item.id) === Number(group.versions?.representative_id),
      );
      if (!representative) return [];
      return [{
        ...representative,
        conceptLabel: group.label || representative.title || "Untitled concept",
        canGenerate: Boolean(
          representative.content?.trim()
          && group.versions?.classification_complete !== false,
        ),
      }];
    }),
    [groups],
  );

  async function waitForGeneration(runId) {
    let result;
    for (let attempt = 0; attempt < 300; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
      result = await fetchQuestionGenerationTrace(runId);
      if (["finished", "failed"].includes(result.run.status)) break;
    }
    if (!result || result.run.status === "running") {
      throw new Error("Question generation is still running. Refresh this page shortly.");
    }
    if (result.run.status === "failed") {
      const failure = [...(result.events || [])].reverse().find((event) => event.event_type === "error");
      throw new Error(failure?.message || "Question generation failed.");
    }
  }

  async function handleGenerateObject(item) {
    if (!item.canGenerate) return;
    setGeneratingKey(`object-${item.id}`);
    setGenerationProgress("");
    onError("");
    onMessage(`Generating and classifying questions for “${item.title}”.`);
    try {
      const started = await startQuestionGeneration(item.material, item.id);
      await waitForGeneration(started.run_id);
      onResourcesChange(await fetchLearningResources(courseId, topicId));
      onMessage(`Questions for “${item.title}” were generated, classified as LOTS/HOTS, and saved.`);
    } catch (err) {
      onError(err.message);
    } finally {
      setGeneratingKey("");
    }
  }

  async function handleGenerateAll() {
    const eligibleObjects = learningObjects.filter((item) => item.canGenerate);
    if (!eligibleObjects.length) return;
    setGeneratingKey("all");
    onError("");
    onMessage("Generating one question bank from the Normal version of each concept.");
    try {
      for (let index = 0; index < eligibleObjects.length; index += 1) {
        const item = eligibleObjects[index];
        setGenerationProgress(`${index + 1} of ${eligibleObjects.length}: ${item.conceptLabel}`);
        const started = await startQuestionGeneration(item.material, item.id);
        await waitForGeneration(started.run_id);
      }
      onResourcesChange(await fetchLearningResources(courseId, topicId));
      onMessage("One LOTS/HOTS question bank was generated for each concept from its Normal version.");
    } catch (err) {
      onError(err.message);
    } finally {
      setGeneratingKey("");
      setGenerationProgress("");
    }
  }

  return (
    <div className="question-generation-board">
      <div className="question-generation-board-heading">
        <div>
          <span className="connection-eyebrow">AI question generator</span>
          <h4>Learning objects</h4>
          <p>Generate questions from one concept's Normal version, or generate one question bank for every concept.</p>
        </div>
        <button type="button" className="btn btn-primary" disabled={Boolean(generatingKey) || !learningObjects.some((item) => item.canGenerate)} onClick={handleGenerateAll}>
          {generatingKey === "all" ? "Generating all…" : "Generate all questions"}
        </button>
      </div>
      {generationProgress && <div className="question-generation-progress" role="status">Processing {generationProgress}</div>}
      {!learningObjects.length ? <div className="review-queue-empty">No confirmed learning objects are available yet.</div> : (
        <div className="question-learning-object-list">
          {learningObjects.map((item) => {
            const material = materialById.get(Number(item.material));
            const isGenerating = generatingKey === `object-${item.id}`;
            return (
              <article className="question-learning-object-card" key={item.id}>
                <div className="question-learning-object-copy">
                  <div className="question-learning-object-title">
                    <span aria-hidden="true">LO</span>
                    <div><small>{item.conceptLabel}</small><strong>{item.title}</strong></div>
                  </div>
                  <FormattedLearningObjectContent content={item.content} className="question-learning-object-preview" />
                  <small>Normal source: {material?.filename || material?.title || `PDF ${item.material}`}</small>
                </div>
                <button type="button" className="btn btn-secondary" disabled={Boolean(generatingKey) || !item.canGenerate} onClick={() => handleGenerateObject(item)}>
                  {isGenerating ? "Generating…" : item.canGenerate ? "Generate questions" : "Normal classification required"}
                </button>
              </article>
            );
          })}
        </div>
      )}
    </div>
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
  questionPairings,
  materialById,
  busyAction,
  onEditQuestion,
  onDeleteQuestion,
}) {
  const [uploading, setUploading] = useState(false);
  const [questionType, setQuestionType] = useState("true_false");
  const [prompt, setPrompt] = useState("");
  const [choices, setChoices] = useState(["", "", "", ""]);
  const [correctAnswer, setCorrectAnswer] = useState("True");
  const [conceptGroupId, setConceptGroupId] = useState("");
  const [saving, setSaving] = useState(false);
  const [editingQuestion, setEditingQuestion] = useState(null);
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
    <aside className="match-suggestion-panel panel-aside question-tools-panel" aria-labelledby="manual-question-panel-title">
      <section className="saved-question-sidebar" aria-labelledby="saved-question-title">
        <div className="match-suggestion-heading">
          <div>
            <span className="connection-eyebrow">Question bank</span>
            <h4 id="saved-question-title">Saved questions</h4>
          </div>
          <span>{questionPairings.length}</span>
        </div>
        <p className="question-source-prompt">PDF, manual, and generated questions appear together.</p>
        {!questionPairings.length ? <div className="review-queue-empty">No saved questions yet.</div> : (
          <div className="saved-question-list is-sidebar">
            {questionPairings.map((question) => {
              const link = question.learning_object_links?.[0];
              const group = groups.find((item) => Number(item.id) === Number(link?.learning_object_group_id));
              const material = materialById.get(Number(question.material));
              const concept = group?.label || group?.learning_objects?.[0]?.title || link?.learning_object_title || "No concept assigned";
              return (
                <article className="saved-question-card" key={question.id}>
                  <div className="question-review-meta">
                    <span className={`question-origin-pill is-${question.source_type || "pdf"}`}>
                      {question.source_type === "manual" ? "Manual" : question.source_type === "generated" ? "Generated" : "PDF"}
                    </span>
                    {question.thinking_order && <span className="question-thinking-pill">{question.thinking_order}</span>}
                  </div>
                  <strong>{question.prompt}</strong>
                  <small>{concept}{material?.filename || material?.title ? ` · ${material?.filename || material?.title}` : ""}</small>
                  {question.validation_status === "needs_review" && <span className="saved-question-warning">Details required</span>}
                  <div className="saved-question-actions">
                    <button type="button" className="btn btn-secondary btn-small" disabled={Boolean(busyAction)} onClick={() => setEditingQuestion(question)}>Edit</button>
                    <button type="button" className="btn btn-danger btn-small" disabled={Boolean(busyAction)} onClick={() => onDeleteQuestion(question)}>Delete</button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>

      <div className="question-source-divider" role="separator"><span>ADD QUESTIONS</span></div>
      <div className="match-suggestion-heading">
        <div>
          <span className="connection-eyebrow">Question tools</span>
          <h4 id="manual-question-panel-title">Add questions</h4>
        </div>
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

      {editingQuestion && (
        <div className="question-edit-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setEditingQuestion(null); }}>
          <div className="question-edit-modal" role="dialog" aria-modal="true" aria-labelledby="question-edit-title">
            <div className="question-edit-modal-head"><div><span className="connection-eyebrow">Edit question</span><h3 id="question-edit-title">Question details</h3></div></div>
            <QuestionEditForm question={editingQuestion} groups={groups} busy={Boolean(busyAction)} onCancel={() => setEditingQuestion(null)} onSave={async (values) => {
              const saved = await onEditQuestion(editingQuestion, values);
              if (saved) setEditingQuestion(null);
            }} />
          </div>
        </div>
      )}
    </aside>
  );
}

// Publishing can run for many minutes. Showing which stage is working, and
// how far through it is, is the difference between "slow" and "hung".
function PublishTrace({ events, running }) {
  const latest = events[events.length - 1];
  const progress = [...events].reverse().find((e) => e.data?.total);
  const failures = events.filter(
    (e) => e.event_type === "audio_failed" || e.event_type === "publish_failed",
  );

  return (
    <div className="publish-trace" role="status" aria-live="polite">
      <div className="publish-trace-head">
        <strong>{running ? "Publishing" : "Publish finished"}</strong>
        {progress?.data && (
          <span className="publish-trace-count">
            step {progress.data.index} of {progress.data.total}
          </span>
        )}
      </div>
      {latest && <p className="publish-trace-current">{latest.message}</p>}
      {failures.length > 0 && (
        <ul className="publish-trace-failures">
          {failures.map((e) => (
            <li key={e.seq}>{e.message}</li>
          ))}
        </ul>
      )}
      <details className="publish-trace-log">
        <summary>Full trace ({events.length})</summary>
        <ol>
          {events.map((e) => (
            <li key={e.seq}>{e.message}</li>
          ))}
        </ol>
      </details>
    </div>
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
  const [publishEvents, setPublishEvents] = useState([]);
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

  // Publishing narrates images, settles versions and synthesises audio, each
  // of which calls a local model. The request only starts the run; progress
  // arrives by polling the run's event stream so the teacher can see which
  // stage is working rather than watching a spinner for several minutes.
  async function handlePublish() {
    setPublishing(true);
    setPublishEvents([]);
    onError("");
    onMessage("");
    try {
      const started = await publishTopic(courseId, topicId);
      await followPublishRun(started.run_id);
    } catch (err) {
      onError(err.message);
      setPublishing(false);
    }
  }

  async function followPublishRun(runId) {
    let after = 0;
    while (true) {
      let payload;
      try {
        payload = await fetchGenerationRunEvents(runId, after);
      } catch (err) {
        onError(`Lost contact with the publish run: ${err.message}`);
        setPublishing(false);
        return;
      }

      const incoming = payload.events || [];
      if (incoming.length) {
        after = incoming[incoming.length - 1].seq;
        setPublishEvents((current) => [...current, ...incoming]);
      }

      const status = payload.run?.status;
      if (status === "finished" || status === "failed") {
        setPublishing(false);
        const summary = incoming.find((e) => e.event_type === "publish_finished")?.data?.summary;
        if (status === "failed") {
          onError("Publishing did not complete. Resolve the content or audio errors in the trace below, then retry.");
        } else if (summary) {
          const audio = summary.audio_generated_count || 0;
          const materials = summary.materials_processed || 0;
          const incomplete = (summary.incomplete_versions || []).length;
          onMessage(
            `Published. ${audio} audio file${audio === 1 ? "" : "s"} across `
            + `${materials} lesson file${materials === 1 ? "" : "s"}.`
            + (incomplete ? ` ${incomplete} concept${incomplete === 1 ? "" : "s"} still missing a version.` : ""),
          );
        } else {
          onMessage("Published.");
        }
        try {
          const refreshed = await fetchLearningResources(courseId, topicId);
          onResourcesChange(refreshed);
        } catch {
          // The run is what matters; a stale panel is recoverable by reloading.
        }
        return;
      }

      await new Promise((resolve) => setTimeout(resolve, 2000));
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
      <div className="review-step-indicator has-four-steps" aria-label="Review progress">
        <span className="is-complete">1</span>
        <div aria-hidden="true" />
        <span className="is-complete">2</span>
        <div aria-hidden="true" />
        <span className="is-complete">3</span>
        <div aria-hidden="true" />
        <span className="is-active">4</span>
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
        {(publishing || publishEvents.length > 0) && (
          <PublishTrace events={publishEvents} running={publishing} />
        )}
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
  const [versionGenerationEvents, setVersionGenerationEvents] = useState([]);
  const [filter, setFilter] = useState("all");
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedIds, setSelectedIds] = useState([]);
  const [groupLabel, setGroupLabel] = useState("");
  const automaticClassificationRef = useRef("");

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
  const unclassifiedGroupSignature = groups
    .filter((group) => group.versions?.classification_complete === false)
    .map((group) => group.id)
    .join(",");
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
    if (reviewStep !== "versions") automaticClassificationRef.current = "";
  }, [reviewStep]);

  useEffect(() => {
    if (
      reviewStep !== "versions"
      || loading
      || busyAction
      || !unclassifiedGroupSignature
    ) return;
    const runKey = `${topicId}:${unclassifiedGroupSignature}`;
    if (automaticClassificationRef.current === runKey) return;
    automaticClassificationRef.current = runKey;
    generateAllVersions();
  }, [reviewStep, loading, busyAction, topicId, unclassifiedGroupSignature]);

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

  async function assignVersion(learningObjectId, slot) {
    setBusyAction(`version-assign-${learningObjectId}`);
    onError("");
    onMessage("");
    try {
      const data = await assignVersionSlot(courseId, topicId, learningObjectId, slot);
      setResources(data);
      onMessage(
        slot === "EXTRA"
          ? "Kept as an extra version for the learning path."
          : `Set as the ${slot.toLowerCase()} version.`
            + (data.version_assignment?.moved_to_extra ? " The previous source is now under Other source versions." : ""),
      );
      return true;
    } catch (err) {
      onError(err.message);
      return false;
    } finally {
      setBusyAction("");
    }
  }

  // One object per request. The model takes minutes, so the button this drives
  // says so rather than looking hung.
  async function generateVersions(learningObjectId, slot) {
    setBusyAction(`version-generate-${learningObjectId}-${slot.toLowerCase()}`);
    onError("");
    onMessage("");
    try {
      const data = await generateObjectVersions(courseId, topicId, learningObjectId, slot);
      setResources(data);
      const result = data.version_generation || {};
      if (result.errors?.length) {
        onError(result.errors[0].detail || "Version generation failed.");
      } else if (result.generated?.length) {
        onMessage(`Wrote the ${result.generated.map((s) => s.toLowerCase()).join(" and ")} version.`);
      } else {
        onMessage(`The ${slot.toLowerCase()} version already exists.`);
      }
      return true;
    } catch (err) {
      onError(err.message);
      return false;
    } finally {
      setBusyAction("");
    }
  }

  async function generateAllMissingVersions() {
    const targets = groups.flatMap((group) => {
      if (
        group.versions?.classification_complete === false
        || !group.versions?.representative_id
      ) return [];
      const slots = group.versions?.slots || {};
      const missingCount = (slots.simplified ? 0 : 1) + (slots.elaborated ? 0 : 1);
      return missingCount
        ? [{ id: group.versions.representative_id, missingCount }]
        : [];
    });
    if (!targets.length) return;
    const totalMissing = targets.reduce((count, target) => count + target.missingCount, 0);

    setBusyAction("version-generate-missing-all");
    onError("");
    onMessage(`Generating ${totalMissing} missing version${totalMissing === 1 ? "" : "s"}.`);
    let generatedCount = 0;
    const failures = [];
    try {
      for (const target of targets) {
        try {
          const data = await generateObjectVersions(
            courseId,
            topicId,
            target.id,
          );
          setResources(data);
          const result = data.version_generation || {};
          generatedCount += result.generated?.length || 0;
          failures.push(...(result.errors || []));
        } catch (err) {
          failures.push({ detail: err.message });
        }
      }
      onMessage(
        `Generated ${generatedCount} missing version${generatedCount === 1 ? "" : "s"}.`,
      );
      if (failures.length) {
        onError(
          `${failures.length} version${failures.length === 1 ? "" : "s"} could not be generated. ${failures[0].detail || ""}`.trim(),
        );
      }
    } finally {
      setBusyAction("");
    }
  }

  async function generateAllVersions() {
    setBusyAction("version-generate-all");
    setVersionGenerationEvents([]);
    onError("");
    onMessage("Classifying existing PDF source variants in the background.");
    try {
      const started = await generateAllObjectVersions(courseId, topicId);
      let after = 0;
      let allEvents = [];
      while (true) {
        const payload = await fetchGenerationRunEvents(started.run_id, after);
        const incoming = payload.events || [];
        if (incoming.length) {
          after = incoming[incoming.length - 1].seq;
          allEvents = [...allEvents, ...incoming];
          setVersionGenerationEvents(allEvents);
        }
        if (["finished", "failed"].includes(payload.run?.status)) {
          if (payload.run.status === "failed") {
            const failure = [...allEvents].reverse().find(
              (event) => event.event_type === "versions_bulk_failed",
            );
            throw new Error(failure?.message || "PDF variant classification failed.");
          }
          const finished = [...allEvents].reverse().find(
            (event) => event.event_type === "versions_bulk_finished",
          );
          const summary = finished?.data?.summary || {};
          setResources(await fetchLearningResources(courseId, topicId));
          onMessage(
            `${summary.source_variant_count || 0} PDF variant${summary.source_variant_count === 1 ? " was" : "s were"} classified, including ${summary.extra_count || 0} extra${summary.extra_count === 1 ? "" : "s"}. `
            + "Use Generate only on any Simplified or Elaborated slot that is still missing."
            + (summary.errors?.length ? ` ${summary.errors.length} concept${summary.errors.length === 1 ? "" : "s"} could not be completed.` : ""),
          );
          break;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
      }
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  async function saveVersionText(variantId, narration) {
    setBusyAction(`version-edit-${variantId}`);
    onError("");
    onMessage("");
    try {
      const data = await editVersionText(courseId, topicId, variantId, narration);
      setResources(data);
      onMessage("Version wording updated.");
      return true;
    } catch (err) {
      onError(err.message);
      return false;
    } finally {
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

  async function deleteQuestion(question) {
    if (!window.confirm(`Delete this question permanently?\n\n${question.prompt}`)) return false;
    setBusyAction(`question-delete-${question.id}`);
    onError("");
    onMessage("");
    try {
      setResources(await deleteTopicQuestion(courseId, topicId, question.id));
      onMessage("Question deleted.");
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
          {(resources?.grouping_warnings || []).map((warning) => (
            <p role="alert" key={warning}>{warning}</p>
          ))}
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
                                      ? "No image narration is available. Start Ollama, then confirm again to retry."
                                      : "Image narration included."}
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
      {reviewStep === "versions" && (
        <VersionReviewPanel
          groups={groups}
          materialById={materialById}
          busyAction={busyAction}
          onReviewStepChange={onReviewStepChange}
          onAssignSlot={assignVersion}
          onGenerate={generateVersions}
          onGenerateAll={generateAllMissingVersions}
          generationEvents={versionGenerationEvents}
          onEditVersion={saveVersionText}
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
            generationProps={{
              courseId,
              topicId,
              onResourcesChange: setResources,
              onError,
              onMessage,
              lessonMaterials: materials.filter(
                (material) => material.generated_json?.learning_objects_confirmed && material.learning_objects?.length,
              ),
            }}
          />
          <ManualQuestionPanel
            courseId={courseId}
            topicId={topicId}
            groups={groups}
            onResourcesChange={setResources}
            onCourseChange={onCourseChange}
            onError={onError}
            onMessage={onMessage}
            lessonMaterials={materials.filter(
              (material) => material.generated_json?.learning_objects_confirmed && material.learning_objects?.length,
            )}
            questionPairings={allQuestionPairings}
            materialById={materialById}
            busyAction={busyAction}
            onEditQuestion={editQuestion}
            onDeleteQuestion={deleteQuestion}
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
  const imageNarrationRepairAttempts = useRef(new Set());

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
  const missingImageNarrationKey = imagesMissingDescription.map((item) => item.id).join(",");

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

  useEffect(() => {
    if (!learningObjectsConfirmed || !missingImageNarrationKey) return;

    const attemptKey = `${material.id}:${missingImageNarrationKey}`;
    if (imageNarrationRepairAttempts.current.has(attemptKey)) return;
    imageNarrationRepairAttempts.current.add(attemptKey);

    setBusyAction("image-narration");
    regenerateImageNarrations(courseId, material.id)
      .then((updatedCourse) => {
        onCourseChange(updatedCourse);
        const result = updatedCourse.image_description_generation;
        if (result?.generated_count) {
          onMessage(
            `Generated ${result.generated_count} missing picture narration${result.generated_count === 1 ? "" : "s"} with Gemma.`
          );
        } else if (result?.errors?.length) {
          onError(result.errors[0].detail || "Gemma could not generate the picture narration.");
        }
      })
      .catch((err) => onError(err.message))
      .finally(() => setBusyAction(""));
  }, [courseId, learningObjectsConfirmed, material.id, missingImageNarrationKey, onCourseChange, onError, onMessage]);

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
      const imageResult = updatedCourse.image_description_generation;
      if (imageResult?.generated_count) {
        onMessage(`Learning objects confirmed. Generated ${imageResult.generated_count} missing image narration${imageResult.generated_count === 1 ? "" : "s"} with Gemma.`);
      } else if (imageResult?.errors?.length) {
        onMessage("Learning objects confirmed, but image narration is still unavailable. Check that Ollama and gemma3:4b are running, then confirm again.");
      } else {
        onMessage("Learning objects confirmed and saved to the database.");
      }
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
                                    ? `Learning object ${index + 1} has no image narration. Confirm again to retry Gemma, or edit it manually.`
                                    : "Image narration included."}
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
      (material) => (
        material.status !== "failed"
        && !material.generated_json?.learning_objects_confirmed
      ),
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
              <Link className="topic-path-link" to={`/courses/${courseId}/topics/${topic.id}/path`}>
                View learning path
              </Link>
            </div>
            {!(activeSource === "connections" && ["versions", "questions"].includes(connectionReviewStep)) && (
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
