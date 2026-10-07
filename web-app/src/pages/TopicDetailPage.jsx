import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { uploadedMaterialFromResponse } from "../uploadNavigation";
import {
  acceptLearningObjectMatchSuggestion,
  applyRegrouping,
  confirmLearningObjects,
  createLearningObject,
  createTopicQuestion,
  deleteLearningMaterial,
  deleteLearningObject,
  keepQuestionBank,
  labelTopicQuestions,
  deleteTopicLearningObject,
  deleteTopicQuestion,
  fetchCourse,
  fetchLearningResources,
  fetchQuestionGenerationTrace,
  fetchRegroupingPreview,
  fetchTopicLearningPath,
  generateAudioPlaylist,
  generateAllObjectVersions,
  fetchGenerationRunEvents,
  publishTopic,
  rejectLearningObjectMatchSuggestion,
  assignVersionSlot,
  editVersionText,
  removeVersion,
  generateObjectVersions,
  keepVersionText,
  moveObjectOut,
  moveObjectToConcept,
  reviewQuestionPairing,
  startQuestionGeneration,
  startTopicQuestionGeneration,
  updateLearningObject,
  updateTopicQuestion,
  uploadLearningMaterial,
} from "../api";
// The same path display the standalone page uses, so review step 5 and that
// page cannot drift apart.
import { MaterialPath } from "./LearningPathPage";
import BundleParts from "../learning-path/BundleParts";
import PublishedDialog from "../learning-path/PublishedDialog";

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

function withUnpublishedNote(message, data) {
  return data?.unpublished
    ? `${message} The topic was unpublished; republish it when ready.`
    : message;
}

function isImageLearningObject(item) {
  return item?.kind === "image" || Boolean(item?.image_url);
}

function isQuestionMaterial(material) {
  const generatedJson = material?.generated_json || {};
  return generatedJson.document_role === "assessment"
    || (!(material?.learning_objects?.length) && Boolean(material?.questions?.length));
}

// "Facts and Information" -> "is-facts-and-information", matching the
// modifier classes in pipeline.css for the four-tier Bloom category pill.
function categoryPillClass(category) {
  const slug = String(category || "").trim().toLowerCase().replace(/\s+/g, "-");
  return slug ? `is-${slug}` : "";
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

// The five review steps in order. One tracker is drawn above every step and
// sticks to the top of the panel, so the teacher always sees where they are.
const REVIEW_STEPS = [
  { key: "objects", label: "Object pairs" },
  { key: "versions", label: "Content versions" },
  { key: "questions", label: "Question pairs" },
  { key: "publish", label: "Content & questions" },
  { key: "path", label: "Learning path" },
];

function ReviewSteps({ step }) {
  const current = REVIEW_STEPS.findIndex((item) => item.key === step);
  return (
    <div className="review-step-indicator has-five-steps is-sticky" aria-label="Review progress">
      {REVIEW_STEPS.map((item, index) => (
        <Fragment key={item.key}>
          {index > 0 && <div aria-hidden="true" />}
          <span
            className={index === current ? "is-active" : index < current ? "is-complete" : undefined}
            aria-current={index === current ? "step" : undefined}
          >
            {index + 1}
          </span>
        </Fragment>
      ))}
      <strong>{REVIEW_STEPS[current]?.label}</strong>
    </div>
  );
}

// Back and Next for a review step, pinned to the bottom-right of the window so
// moving on never means scrolling to the end of a long list. Rendered into
// <body> so no scrolling panel around it can carry it away.
function StepDock({ children }) {
  return createPortal(<div className="review-step-dock">{children}</div>, document.body);
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
                      {(suggestion.source_members?.length ? suggestion.source_members : [source]).map((member) => (
                        <div key={member.id} className="match-unit-member">
                          <strong>{member.title}</strong>
                          {member.image_url && (
                            <img className="review-source-image" src={member.image_url} alt={member.title || "Source A"} />
                          )}
                          <p className="match-source-content">{member.content || "No narration content."}</p>
                        </div>
                      ))}
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
                      {(suggestion.candidate_members?.length ? suggestion.candidate_members : [candidate]).map((member) => (
                        <div key={member.id} className="match-unit-member">
                          <strong>{member.title}</strong>
                          {member.image_url && (
                            <img className="review-source-image" src={member.image_url} alt={member.title || "Source B"} />
                          )}
                          <p className="match-source-content">{member.content || "No narration content."}</p>
                        </div>
                      ))}
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
      <StepDock>
        <button type="button" className="btn btn-primary" disabled={Boolean(busyAction)} onClick={() => onReviewStepChange("versions")}>
          Next step: Content versions
        </button>
      </StepDock>
    </aside>
  );
}


// The three versions a learner is offered, in the order they are shown.
export const VERSION_ROLES = [
  { key: "standard", label: "Standard" },
  { key: "simplified", label: "Simplified" },
  { key: "elaborated", label: "Elaborated" },
];

// What still stands between the teacher and the learning path: concepts
// missing a Simplified or Elaborated version, and concepts with no question.
// The Question pairs step waits on the first; the Learning path button on both.
// A printed question still waiting for its Bloom level, LOTS/HOTS and category.
export function isUnlabelled(question) {
  return (question.source_type || "pdf") === "pdf" && !question.bloom_level;
}

// Every concept needs a minimum of LOTS and HOTS questions before the teacher
// moves past the Questions step. Both the minimum and the counts come from the
// server, which also decides what counts: generated questions, the teacher's
// own, and printed ones the teacher has edited and confirmed.
export function isShortOfQuestions(group) {
  const counts = group?.question_counts || {};
  const minimum = group?.question_minimum || {};
  return Object.keys(minimum).some((tier) => (counts[tier] || 0) < minimum[tier]);
}

export function minimumText(minimum) {
  return minimum ? `${minimum.LOT} LOTS and ${minimum.HOT} HOTS` : "the minimum questions";
}

export function contentGaps(groups, questions = []) {
  const concepts = (groups || []).filter((group) => group.versions?.representative_id);
  const missingVersions = concepts.filter((group) => (
    group.versions?.classification_complete === false
    || !group.versions?.slots?.simplified
    || !group.versions?.slots?.elaborated
  )).length;
  const shortQuestions = concepts.filter(isShortOfQuestions).length;
  const outOfDateQuestions = concepts.filter((group) => group.question_bank_out_of_date).length;
  const unlabelledQuestions = questions.filter(isUnlabelled).length;
  return {
    missingVersions,
    shortQuestions,
    outOfDateQuestions,
    unlabelledQuestions,
    minimumText: minimumText(concepts[0]?.question_minimum),
    ready: concepts.length > 0 && missingVersions === 0 && shortQuestions === 0
      && outOfDateQuestions === 0 && unlabelledQuestions === 0,
  };
}

// "3 versions from 2 files" rather than "6 variations": the old count was the
// concept's object count, which is not the number of versions a learner is
// offered and read as though the lesson said six different things.
function versionSummary(group) {
  const slots = group.versions?.slots || {};
  const present = VERSION_ROLES.filter(({ key }) => slots[key]);
  const files = new Set(
    present
      .filter((role) => slots[role.key].source !== "generated")
      .map((role) => slots[role.key].material),
  );
  const versions = `${present.length} version${present.length === 1 ? "" : "s"}`;
  if (!files.size) return versions;
  return `${versions} from ${files.size} file${files.size === 1 ? "" : "s"}`;
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

// Why a question rated "create" stays out of the learner quiz.
const NOT_SERVED_TIP = "MAVIA maps Bloom's levels to the learner quiz's two tiers: remember, "
  + "understand and apply are LOTS; analyze and evaluate are HOTS. \u201cCreate\u201d has no tier "
  + "in the quiz, so by default these questions stay in your bank but are not given to learners.";

function ReviewQueuePanel({
  questionPairings,
  groups,
  materialById,
  busyAction,
  onReviewStepChange,
  generationProps,
  manualPanel,
  unlabelledCount = 0,
  labelling = { running: false, error: "" },
  onRetryLabelling,
}) {
  // Final review waits until every concept has its minimum LOTS and HOTS,
  // no bank is out of date, and every printed question is labelled.
  const gaps = contentGaps(groups, questionPairings);
  const blockers = [
    unlabelledCount > 0 && `${unlabelledCount} printed question${unlabelledCount === 1 ? " still needs" : "s still need"} labelling`,
    gaps.shortQuestions > 0 && `${gaps.shortQuestions} concept${gaps.shortQuestions === 1 ? " needs" : "s need"} ${gaps.minimumText}`,
    gaps.outOfDateQuestions > 0 && `${gaps.outOfDateQuestions} concept${gaps.outOfDateQuestions === 1 ? " has" : "s have"} questions to check`,
  ].filter(Boolean);
  // Both views stay mounted so a running generation or a half-written
  // question survives switching tabs.
  const [mode, setMode] = useState("generate");
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

      {labelling.running && (
        <p role="status" className="connection-unpublished-note">
          Labelling {unlabelledCount} printed question{unlabelledCount === 1 ? "" : "s"} with
          their Bloom level, LOTS/HOTS and category…
        </p>
      )}
      {labelling.error && (
        <div className="error-banner" role="alert">
          {labelling.error}{" "}
          <button type="button" className="btn btn-small btn-secondary" onClick={onRetryLabelling}>
            Try again
          </button>
        </div>
      )}
      <div className="question-mode-tabs" role="tablist" aria-label="How to add questions">
        {[["generate", "Generate"], ["manual", "Manual"]].map(([key, label]) => (
          <button
            type="button"
            role="tab"
            key={key}
            id={`question-mode-${key}`}
            aria-selected={mode === key}
            aria-controls={`question-mode-${key}-panel`}
            onClick={() => setMode(key)}
          >
            {label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id="question-mode-generate-panel"
        aria-labelledby="question-mode-generate"
        hidden={mode !== "generate"}
      >
        <QuestionGenerationTool
          {...generationProps}
          groups={groups}
          materialById={materialById}
        />
      </div>
      <div
        role="tabpanel"
        id="question-mode-manual-panel"
        aria-labelledby="question-mode-manual"
        hidden={mode !== "manual"}
      >
        {manualPanel}
      </div>

      <StepDock>
        <button
          type="button"
          className="btn btn-secondary"
          disabled={Boolean(busyAction)}
          onClick={() => onReviewStepChange("versions")}
        >
          Back to content versions
        </button>
        {blockers.length > 0 && (
          <small className="review-step-dock-note">{blockers.join("; ")}</small>
        )}
        <button
          type="button"
          className="btn btn-primary"
          disabled={Boolean(busyAction) || blockers.length > 0 || labelling.running}
          title={blockers.length ? `Before final review: ${blockers.join("; ")}.` : undefined}
          onClick={() => onReviewStepChange("publish")}
        >
          Next: Final Review
        </button>
      </StepDock>
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
        {groups.map((group) => <option value={group.id} key={group.id}>{group.display_title || group.label || group.learning_objects?.[0]?.title || "Untitled concept"}</option>)}
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
  // Not an AssignedBy choice: a bundle a newer teacher decision pushed out of
  // this slot. Said plainly, because the teacher never chose this role.
  if (entry.assigned_by === "displaced_by_teacher") return `${source} · Moved out of its slot`;
  return source;
}

function VersionSlotCard({
  slotKey,
  heading,
  entry,
  text,
  // The objects a bundle version is taught as; shown one by one when more than one.
  parts = null,
  originLabel,
  readOnly,
  busy,
  isEditing,
  onBeginEdit,
  onCancelEdit,
  onSave,
  onGenerate,
  onDelete,
  // Written from Standard text that has since changed. Publishing waits until the
  // teacher keeps, edits or regenerates it.
  stale = false,
  // No generated version passed the quality check, so learners hear the
  // Standard text at this level. Publishing is not held back; the teacher may
  // write an explanation of their own.
  fallback = false,
  fallbackCount = 0,
  segmentCount = 0,
  busyLabel = "",
  onKeep,
  onRegenerate,
  actions,
}) {
  const [draft, setDraft] = useState(text || "");
  useEffect(() => { setDraft(text || ""); }, [text, isEditing]);

  return (
    <article className={`version-slot is-${slotKey} ${stale ? "is-stale" : ""}`.trim()}>
      <header className="version-slot-head">
        <span className={`version-slot-label is-${slotKey}`}>{heading}</span>
        {originLabel && <small className="version-slot-origin">{originLabel}</small>}
        {actions && <div className="version-slot-role-top">{actions}</div>}
      </header>

      {stale && text && !isEditing && (
        <div className="version-slot-stale" role="alert">
          <strong>Check this version</strong>
          <p>
            The Standard text was changed after this was written, so it may no longer match.
            Publishing waits until you decide.
          </p>
          <div className="version-slot-actions">
            <button type="button" className="btn btn-primary btn-small" disabled={busy} onClick={onKeep}>
              Keep as is
            </button>
            <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={onRegenerate}>
              Regenerate
            </button>
          </div>
          {busy && busyLabel && <small className="muted-text">{busyLabel}</small>}
        </div>
      )}

      {fallback && !stale && !isEditing && (
        <div className="version-slot-stale version-slot-fallback" role="status">
          <strong>{fallbackCount > 0 && fallbackCount < segmentCount
            ? `${fallbackCount} of ${segmentCount} parts use the Standard text`
            : "Using the Standard text for now"}</strong>
          <p>
            {slotKey === "simplified"
              ? "Some Simplified wording did not pass the quality check (easier to read, keeps every fact). "
              : "Some Elaborated wording did not pass the quality check (fuller, keeps every fact). "}
            {fallbackCount > 0 && fallbackCount < segmentCount
              ? "Those parts use their Standard wording; the other parts use generated wording."
              : "Learners at this level hear the Standard text."}
            {" You can write your own explanation instead."}
          </p>
          {!readOnly && (
            <div className="version-slot-actions">
              <button type="button" className="btn btn-primary btn-small" disabled={busy} onClick={onBeginEdit}>
                Write your own explanation
              </button>
            </div>
          )}
        </div>
      )}

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
            {(parts || []).length > 0
              ? <BundleParts parts={parts} className="version-slot-text" showSingleTitle />
              : <p className="version-slot-text">{text}</p>}
            {(!readOnly || onDelete) && (
              <div className="version-slot-actions">
                {!readOnly && (
                  <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={onBeginEdit}>
                    Edit wording
                  </button>
                )}
                {onDelete && (
                  <button type="button" className="btn btn-danger btn-small" disabled={busy} onClick={onDelete}>
                    {busy ? "Removing..." : "Remove version"}
                  </button>
                )}
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
  const roles = ["STANDARD", "SIMPLIFIED", "ELABORATED"]
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
            {slot === "STANDARD" ? "Make Standard"
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
  // Owned by the parent, because the loop that writes the missing versions
  // runs there. This panel only displays it.
  missingProgress,
  onEditVersion,
  onKeepVersion,
  onRegenerateVersion,
  onRemoveVersion,
}) {
  const [chunkIndex, setChunkIndex] = useState(0);
  const [editingSlot, setEditingSlot] = useState(null);

  const chunks = useMemo(
    () => groups.filter((group) => group.versions?.representative_id),
    [groups],
  );
  // Concepts holding a version written from Standard text that has since changed.
  // Publishing refuses these, so they are counted and reachable in one click
  // rather than left for the teacher to find by paging through every concept.
  const staleChunkIndexes = chunks
    .map((item, index) => (
      ["simplified", "elaborated"].some((slot) => item.versions?.slots?.[slot]?.stale) ? index : -1
    ))
    .filter((index) => index >= 0);
  const staleVersionCount = chunks.reduce(
    (count, item) => count + ["simplified", "elaborated"]
      .filter((slot) => item.versions?.slots?.[slot]?.stale).length,
    0,
  );

  function goToNextStale() {
    if (!staleChunkIndexes.length) return;
    const next = staleChunkIndexes.find((index) => index > chunkIndex) ?? staleChunkIndexes[0];
    setChunkIndex(next);
  }

  useEffect(() => {
    setChunkIndex((current) => Math.max(0, Math.min(current, chunks.length - 1)));
  }, [chunks.length]);

  const chunk = chunks[chunkIndex];
  useEffect(() => { setEditingSlot(null); }, [chunk?.id]);

  const versions = chunk?.versions;
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
  // Question pairs wait until every concept has all three versions, whether a
  // PDF supplied it or it was generated.
  const incompleteCount = contentGaps(chunks).missingVersions;
  const representative = chunk?.learning_objects?.find(
    (item) => Number(item.id) === Number(versions?.representative_id),
  );
  const standardEntry = versions?.slots?.standard;
  const originalMaterial = materialById.get(Number(standardEntry?.material ?? representative?.material));

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
        </div>
        <div className="version-review-heading-actions">
          {staleVersionCount > 0 && (
            <button
              type="button"
              className="btn btn-secondary btn-small version-stale-jump"
              disabled={Boolean(busyAction)}
              onClick={goToNextStale}
              title="Versions written before their Standard text was changed. Publishing waits until each is checked."
            >
              {staleVersionCount} version{staleVersionCount === 1 ? "" : "s"} to check · Go to next
            </button>
          )}
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
        <RunProgress
          events={generationEvents}
          running={busyAction === "version-generate-all"}
          runningLabel="Classifying existing PDF variants"
          doneLabel="Classification finished"
          unit="concept"
        />
      )}
      {missingProgress && (
        <RunProgress
          events={[]}
          running
          runningLabel="Writing missing versions"
          doneLabel="Versions written"
          unit="concept"
          index={missingProgress.index}
          total={missingProgress.total}
          current={
            missingProgress.label
              ? `Writing the simplified and elaborated versions of “${missingProgress.label}”`
              : "Starting…"
          }
        />
      )}

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

          <h4 className="version-chunk-title">{chunk.display_title || chunk.label || representative?.title || "Untitled concept"}</h4>
          <p className="muted-text">
            {chunk.learning_objects.length} grouped PDF variant{chunk.learning_objects.length === 1 ? "" : "s"}
            {versions?.classification_complete === false
              ? " — the first relevant PDF is Standard unless the teacher replaces it; supplementary PDFs await classification"
              : " — the primary PDF is Standard; supplementary PDFs may supply Simplified or Elaborated"}.
          </p>

          {versions?.standard_replacement_needed && (
            <div className="version-pending-decision">
              <p><strong>The previous Standard PDF no longer supplies this concept.</strong> Choose a surviving PDF as the new baseline. MAVIA will not promote one automatically.</p>
              <div className="version-slot-actions">
                {(chunk.bundles || []).filter((bundle) => bundle.learning_objects?.length).map((bundle) => {
                  const material = materialById.get(Number(bundle.material));
                  return (
                    <button
                      type="button"
                      className="btn btn-primary btn-small"
                      key={bundle.material}
                      disabled={Boolean(busyAction)}
                      onClick={() => onAssignSlot(bundle.learning_objects[0].id, "STANDARD")}
                    >
                      Use {material?.filename || material?.title || `PDF ${bundle.material}`} as Standard
                    </button>
                  );
                })}
              </div>
            </div>
          )}
          {versions?.classification_complete === false && !versions?.standard_replacement_needed ? (
            <div className="review-queue-empty">
              <strong>Existing PDF variants — not generated:</strong>
              <div className="version-slot-grid">
                {chunk.learning_objects.map((item, index) => {
                  const material = materialById.get(Number(item.material));
                  return (
                    <VersionSlotCard
                      key={item.id}
                      slotKey="unassigned"
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
                Gemma is proposing roles for supplementary PDFs. Afterward, each missing
                Simplified or Elaborated role will have its own Generate button.
              </p>
            </div>
          ) : (versions?.needs_confirmation || []).map((pending) => {
            const candidate = chunk.learning_objects.find(
              (item) => Number(item.id) === Number(pending.learning_object_id),
            );
            if (!candidate) return null;
            const candidateBundle = chunk.bundles?.find(
              (bundle) => Number(bundle.material) === Number(pending.material_id),
            );
            const candidateParts = (candidateBundle?.learning_objects || [candidate])
              .map((item) => ({
                id: item.id,
                title: item.title,
                text: item.content?.trim(),
              }))
              .filter((item) => item.text);
            const candidateText = candidateParts
              .map((item) => item.text)
              .filter(Boolean)
              .join("\n") || candidate.content;
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
                {(pending.review_issues || []).length > 0 && (
                  <p className="muted-text">
                    {pending.review_measures?.weakly_covered_sentences > 0
                      ? `${pending.review_measures.weakly_covered_sentences} Standard sentence(s) may be missing or phrased very differently. `
                      : "The automatic checks disagree with the AI label. "}
                    Compare this PDF text with Standard before choosing a role.
                  </p>
                )}
                <div className="version-pending-source">
                  <BundleParts
                    parts={candidateParts}
                    fallback={candidateText}
                    className="version-pending-parts"
                    showSingleTitle
                  />
                </div>
                <div className="version-slot-actions">
                  {["STANDARD", "SIMPLIFIED", "ELABORATED"].map((slot) => (
                    <button
                      type="button"
                      key={slot}
                      className="btn btn-primary btn-small"
                      disabled={Boolean(busyAction)}
                      onClick={() => onAssignSlot(pending.learning_object_id, slot)}
                    >
                      {slot === "STANDARD" ? "Make Standard"
                        : slot === "SIMPLIFIED" ? "Use as Simplified" : "Use as Elaborated"}
                    </button>
                  ))}
                </div>
                <p className="muted-text">
                  If neither role fits, leave this source unassigned or return to object pairs to separate it from this concept.
                </p>
              </div>
            );
          })}

          {versions?.classification_complete !== false && <div className="version-slot-grid">
            <VersionSlotCard
              slotKey="original"
              heading={versions?.original_selected ? "Standard" : "Standard candidate"}
              text={standardEntry?.text || representative?.content}
              parts={standardEntry?.objects}
              originLabel={versions?.original_selected
                ? `Primary PDF: ${originalMaterial?.filename || originalMaterial?.title || "this PDF"}`
                : "Choose a replacement Standard PDF before continuing"}
              readOnly
              busy={false}
            />
            {["simplified", "elaborated"].map((slotKey) => {
              const entry = versions?.slots?.[slotKey];
              const generateKey = `version-generate-${versions?.representative_id}-${slotKey}`;
              const deleteKey = entry
                ? `version-delete-${slotKey}-${entry.id || entry.source_learning_object_id}`
                : "";
              const busyKeys = entry
                ? [`version-edit-${entry.id}`, `version-keep-${entry.id}`, generateKey, deleteKey]
                : [generateKey];
              return (
                <VersionSlotCard
                  key={slotKey}
                  slotKey={slotKey}
                  heading={slotKey === "simplified" ? "Simplified" : "Elaborated"}
                  entry={entry}
                  text={entry?.text}
                  parts={entry?.objects}
                  originLabel={versionOriginLabel(entry, materialTitleFor(entry))}
                  busy={busyKeys.includes(busyAction)}
                  // A version a PDF supplies has no stored row to edit or keep:
                  // it is the other file's own text, corrected by moving its
                  // objects, not by retyping them here.
                  readOnly={Boolean(entry) && !entry.id}
                  stale={Boolean(entry?.stale)}
                  fallback={Boolean(entry?.fallback)}
                  fallbackCount={entry?.fallback_count || 0}
                  segmentCount={entry?.segment_count || 0}
                  busyLabel={busyAction === generateKey ? "Regenerating, this takes a few minutes…" : ""}
                  onKeep={() => onKeepVersion(entry.id)}
                  onRegenerate={() => onRegenerateVersion(versions.representative_id, slotKey.toUpperCase())}
                  isEditing={editingSlot === slotKey}
                  onBeginEdit={() => setEditingSlot(slotKey)}
                  onCancelEdit={() => setEditingSlot(null)}
                  onSave={async (narration) => {
                    const done = await onEditVersion(entry.id, narration);
                    if (done) setEditingSlot(null);
                  }}
                  onGenerate={() => onGenerate(versions.representative_id, slotKey.toUpperCase())}
                  onDelete={entry?.text ? () => onRemoveVersion(entry, slotKey.toUpperCase()) : null}
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

          {(versions?.archived_unassigned || []).length > 0 && (
            <details className="version-archived-block">
              <summary>Archived unassigned wording ({versions.archived_unassigned.length})</summary>
              <p>This older wording was preserved when the fourth version slot was removed. It is not served to learners.</p>
              {versions.archived_unassigned.map((entry) => (
                <VersionSlotCard
                  key={entry.old_variant_id}
                  slotKey="unassigned"
                  heading="Unassigned wording"
                  text={entry.text}
                  readOnly
                  busy={false}
                />
              ))}
            </details>
          )}

        </>
      )}

      <StepDock>
        <button
          type="button"
          className="btn btn-secondary"
          disabled={Boolean(busyAction)}
          onClick={() => onReviewStepChange("objects")}
        >
          Back to object pairs
        </button>
        {incompleteCount > 0 && (
          <small className="review-step-dock-note">
            {incompleteCount} concept{incompleteCount === 1 ? " still needs" : "s still need"} a
            Simplified or Elaborated version
          </small>
        )}
        <button
          type="button"
          className="btn btn-primary"
          disabled={Boolean(busyAction) || incompleteCount > 0}
          title={incompleteCount > 0 ? "Give every concept a Standard, Simplified and Elaborated version first." : undefined}
          onClick={() => onReviewStepChange("questions")}
        >
          Next step: Question pairs
        </button>
      </StepDock>
    </section>
  );
}

// One square per question the tier needs, filled up to the count: red at 1,
// yellow in between, green once the minimum is met. Counts what the server
// counts toward the minimum.
function TierCounter({ label, count, needed }) {
  const filled = Math.min(count, needed);
  const level = count >= needed ? "is-enough" : count <= 1 ? "is-one" : "is-two";
  return (
    <div className="tier-counter" aria-label={`${label}: ${count} of ${needed} needed`}>
      <span className="tier-counter-label">{label} QUESTION COUNTER</span>
      <span className="tier-counter-boxes" aria-hidden="true">
        {Array.from({ length: needed }, (_, index) => (
          <span key={index} className={index < filled ? `tier-box ${level}` : "tier-box"} />
        ))}
      </span>
      {/* Always shown, so every counter reads the same way: at the minimum,
          above it, or short of it. */}
      <small className="tier-counter-extra">{count}</small>
    </div>
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
  // Structured rather than a formatted string, because the progress dialog
  // needs the position to draw a bar and the name to say what it is working on.
  const [outerProgress, setOuterProgress] = useState(null);
  // The run currently being polled. "Generate all" walks the concepts one at a
  // time, so these events describe work inside one concept, while
  // outerProgress tracks the walk across all of them.
  const [runEvents, setRunEvents] = useState([]);
  const learningObjects = useMemo(
    () => (groups || []).flatMap((group) => {
      const representative = (group.learning_objects || []).find(
        (item) => Number(item.id) === Number(group.versions?.representative_id),
      );
      if (!representative) return [];
      return [{
        ...representative,
        conceptLabel: group.display_title || group.label || representative.title || "Untitled concept",
        // The concept's whole Standard text, every object of the Standard PDF, not
        // just its first: that first object is only where the bank is filed.
        content: group.versions?.slots?.standard?.text || representative.content,
        standardParts: group.versions?.slots?.standard?.objects || [],
        // Carried through so each concept can show what was generated from it.
        // Spreading the representative alone dropped these, which is why the
        // board could only ever say "generating" and never "here is the result".
        questions: group.questions || [],
        questionCounts: group.question_counts || {},
        questionMinimum: group.question_minimum || {},
        isShort: isShortOfQuestions(group),
        groupId: group.id,
        questionsOutOfDate: Boolean(group.question_bank_out_of_date),
        canGenerate: Boolean(
          representative.content?.trim()
          && group.versions?.classification_complete !== false,
        ),
      }];
    }),
    [groups],
  );

  // `carried` holds the finished runs before this one. Generating every
  // concept starts a run each, and each one's sequence begins at 1: replacing
  // the trace with only the run in flight made the log collapse to two lines
  // and grow back for every concept, over and over. Its own seq is therefore
  // paired with the run it belongs to, so the accumulated lines stay distinct.
  //
  // `onEvents` sees every poll's events. A topic run lasts as long as its
  // rounds take, so polling stops only when the run ends or stops reporting
  // progress for `stallSeconds`.
  async function waitForGeneration(runId, carried = [], { onEvents, stallSeconds = 300 } = {}) {
    let result;
    let seen = 0;
    let quietSeconds = 0;
    const tag = (events) => (events || []).map(
      (event) => ({ ...event, uid: `${runId}:${event.seq}` }),
    );
    while (quietSeconds < stallSeconds) {
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
      result = await fetchQuestionGenerationTrace(runId);
      const events = result.events || [];
      quietSeconds = events.length > seen ? 0 : quietSeconds + 1;
      seen = events.length;
      // The endpoint returns this run's whole event list each poll, so its own
      // lines replace while everything before them is kept.
      setRunEvents([...carried, ...tag(events)]);
      if (onEvents) await onEvents(events);
      if (["finished", "failed"].includes(result.run.status)) break;
    }
    if (!result || result.run.status === "running") {
      throw new Error("Question generation is still running. Refresh this page shortly.");
    }
    if (result.run.status === "failed") {
      const failure = [...(result.events || [])].reverse().find((event) => event.event_type === "error");
      throw new Error(failure?.message || "Question generation failed.");
    }
    return tag(result.events);
  }

  // Regenerate one out-of-date concept: replaces its questions the teacher
  // has not edited. The only per-concept generation left on this screen.
  async function handleGenerateObject(item) {
    if (!item.canGenerate) return;
    setGeneratingKey(`object-${item.id}`);
    // Named but uncounted. There is one concept, so a percentage would only be
    // theatre -- and the run's own events report "1 of 1", which reads as
    // finished from the first moment.
    setOuterProgress({ index: null, total: null, label: item.conceptLabel });
    onError("");
    onMessage(`Generating and classifying questions for “${item.title}”.`);
    try {
      const started = await startQuestionGeneration(item.material, item.id);
      await waitForGeneration(started.run_id);
      onResourcesChange(await fetchLearningResources(courseId, topicId));
      onMessage(`Questions for “${item.title}” were regenerated, classified as LOTS/HOTS, and saved.`);
    } catch (err) {
      onError(err.message);
    } finally {
      setGeneratingKey("");
    }
  }

  async function handleKeepBank(item) {
    setGeneratingKey(`keep-${item.groupId}`);
    onError("");
    try {
      onResourcesChange(await keepQuestionBank(courseId, topicId, item.groupId));
      onMessage(`Kept the questions for “${item.conceptLabel}” as they are.`);
    } catch (err) {
      onError(err.message);
    } finally {
      setGeneratingKey("");
    }
  }

  // One button for the whole topic. The first click writes every concept's
  // questions; after that it is "Generate more". Either way the server adds
  // questions only to the concepts short of the minimum, and only in the
  // tier they lack. It never replaces questions.
  const hasGenerated = learningObjects.some((item) => (item.questions || []).some(
    (question) => (question.source_type || "pdf") === "generated",
  ));
  const targets = learningObjects.filter((item) => item.canGenerate && item.isShort);
  const goal = minimumText(learningObjects[0]?.questionMinimum);

  // One click, one run: the server goes round by round until every concept
  // meets the minimum or its rounds are used up. The page follows the run's
  // events, and reloads the counters as each concept finishes.
  async function handleGenerateAll() {
    if (!targets.length) return;
    setGeneratingKey("all");
    onError("");
    onMessage(hasGenerated
      ? `Adding questions to the concepts still short of ${goal}.`
      : "Generating questions from the Standard version of each concept.");
    let finishedConcepts = 0;
    try {
      const started = await startTopicQuestionGeneration(topicId);
      const events = await waitForGeneration(started.run_id, [], {
        onEvents: async (latest) => {
          const current = [...latest].reverse().find((event) => event.event_type === "node_started");
          if (current?.data) {
            const { index, total, round, rounds, title } = current.data;
            setOuterProgress({
              index,
              total,
              label: rounds > 1 ? `${title} (round ${round} of ${rounds})` : title,
            });
          }
          const finished = latest.filter(
            (event) => ["node_finished", "node_failed"].includes(event.event_type),
          ).length;
          if (finished > finishedConcepts) {
            finishedConcepts = finished;
            onResourcesChange(await fetchLearningResources(courseId, topicId));
          }
        },
      });
      onResourcesChange(await fetchLearningResources(courseId, topicId));
      onMessage("");
      const outcome = [...events].reverse().find((event) => event.event_type === "topic_finished")?.data || {};
      const failed = outcome.failed || [];
      const stillShort = outcome.still_short || [];
      const problems = [];
      if (failed.length) {
        problems.push(`Questions could not be generated for ${failed.length} concept${failed.length === 1 ? "" : "s"}: ${failed.join("; ")}`);
      }
      if (stillShort.length) {
        problems.push(
          `${stillShort.length} concept${stillShort.length === 1 ? " is" : "s are"} still short of ${goal}: `
          + `${stillShort.join(", ")}. Click Generate more to try again, or add questions in the Manual tab.`,
        );
      }
      if (problems.length) onError(problems.join(" "));
    } catch (err) {
      onError(/already in progress/i.test(err.message)
        ? "A question generation is already running for this topic, started before a page "
          + "reload or in another tab. Wait a few minutes for it to finish, then reload the page."
        : err.message);
    } finally {
      setGeneratingKey("");
      setOuterProgress(null);
    }
  }

  return (
    <div className="question-generation-board">
      <div className="question-generation-board-heading">
        <div>
          <span className="connection-eyebrow">AI question generator</span>
          <h4>Learning objects</h4>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          disabled={Boolean(generatingKey) || !targets.length}
          title={hasGenerated && !targets.length ? `Every concept has ${goal}.` : undefined}
          onClick={handleGenerateAll}
        >
          {generatingKey === "all"
            ? "Generating…"
            : !hasGenerated
              ? "Generate questions"
              : targets.length
                ? `Generate more (${targets.length} concept${targets.length === 1 ? "" : "s"})`
                : "All concepts complete"}
        </button>
      </div>
      {outerProgress && (
        <div className="question-generation-progress" role="status">
          Processing {outerProgress.index} of {outerProgress.total}: {outerProgress.label}
        </div>
      )}
      {(generatingKey || runEvents.length > 0) && (
        <RunProgress
          events={runEvents}
          running={Boolean(generatingKey)}
          runningLabel="Generating questions"
          doneLabel="Generation finished"
          unit="concept"
          // The run in flight covers one concept and always reports "1 of 1".
          // What the teacher is waiting on is the walk across every concept.
          index={outerProgress?.index ?? null}
          total={outerProgress?.total ?? null}
          current={
            outerProgress?.label
              ? `Generating questions for “${outerProgress.label}”`
              : ""
          }
        />
      )}
      {!learningObjects.length ? <div className="review-queue-empty">No confirmed learning objects are available yet.</div> : (
        <div className="question-learning-object-list">
          {learningObjects.map((item) => {
            const material = materialById.get(Number(item.material));
            // Questions that came out of this concept. PDF-extracted ones are
            // the sidebar's business; these belong with what produced them.
            const produced = (item.questions || []).filter(
              (question) => (question.source_type || "pdf") !== "pdf",
            );
            return (
              <article className="question-learning-object-card" key={item.id}>
                <div className="question-learning-object-main">
                  <div className="question-learning-object-copy">
                    <div className="question-learning-object-title">
                      <div><strong>{item.conceptLabel}</strong></div>
                    </div>
                    {(item.standardParts || []).length > 1
                      ? <BundleParts parts={item.standardParts} className="question-learning-object-preview" />
                      : <FormattedLearningObjectContent content={item.content} className="question-learning-object-preview" />}
                    <small>Standard source: {material?.filename || material?.title || `PDF ${item.material}`}</small>
                  </div>
                  {!item.canGenerate && (
                    <small className="muted-text">Waiting for its Standard version to be classified</small>
                  )}
                </div>
                {item.questionsOutOfDate && (
                  <div className="version-slot-stale" role="alert">
                    <strong>Check these questions</strong>
                    <p>
                      This concept's text changed after its questions were written, so some may ask
                      about text that is no longer there. Publishing waits until you decide.
                      Questions you edited are kept if you regenerate.
                    </p>
                    <div className="version-slot-actions">
                      <button
                        type="button"
                        className="btn btn-primary btn-small"
                        disabled={Boolean(generatingKey)}
                        onClick={() => handleKeepBank(item)}
                      >
                        {generatingKey === `keep-${item.groupId}` ? "Keeping..." : "Keep as is"}
                      </button>
                      <button
                        type="button"
                        className="btn btn-secondary btn-small"
                        disabled={Boolean(generatingKey) || !item.canGenerate}
                        onClick={() => handleGenerateObject(item)}
                      >
                        Regenerate
                      </button>
                    </div>
                  </div>
                )}
                <div className="question-learning-object-questions">
                  <div className="question-bank-head">
                    <h5>
                      {produced.length} generated question{produced.length === 1 ? "" : "s"}
                    </h5>
                    <div className="tier-counters">
                      <TierCounter label="LOTS" count={item.questionCounts.LOT || 0} needed={item.questionMinimum.LOT || 0} />
                      <TierCounter label="HOTS" count={item.questionCounts.HOT || 0} needed={item.questionMinimum.HOT || 0} />
                    </div>
                  </div>
                  {!produced.length ? (
                    <p className="question-learning-object-empty">
                      Nothing generated from this concept yet.
                    </p>
                  ) : (
                    <ol>
                      {produced.map((question) => (
                        <li key={question.id}>
                          <div className="generated-question-row">
                            {/* The labels sit on their own line above the
                                question: sharing a line with it squeezed them
                                until "Facts and information" wrapped. */}
                            {(question.thinking_order || question.category) && (
                              <div className="generated-question-meta">
                                {question.thinking_order && (
                                  <span className="question-thinking-pill">{question.thinking_order}</span>
                                )}
                                {question.category && (
                                  <span className={`question-category-pill ${categoryPillClass(question.category)}`}>
                                    {question.category}
                                  </span>
                                )}
                              </div>
                            )}
                            <strong>{question.prompt}</strong>
                          </div>
                          {Boolean(question.choices?.length) && (
                            <ul className="generated-question-choices">
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
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
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

  // This panel is the PDF question bank and nothing else. Generated and manual
  // questions are shown with the concept they belong to, on the generation
  // board, where they can be judged against the content that produced them.
  const pdfQuestions = questionPairings.filter(
    (question) => (question.source_type || "pdf") === "pdf",
  );

  return (
    <aside className="match-suggestion-panel panel-aside question-tools-panel" aria-labelledby="manual-question-panel-title">
      {/* Two explicit columns rather than grid-placing the panel's children by
          selector: the bank is one job and adding a question is the other, and
          wrapping them says so in the markup instead of depending on which
          rule happens to win. */}
      <div className="qt-col qt-col--bank">
      <section className="saved-question-sidebar" aria-labelledby="saved-question-title">
        <div className="match-suggestion-heading">
          <div>
            <span className="connection-eyebrow">Question bank</span>
            <h4 id="saved-question-title">Questions from PDFs</h4>
          </div>
          <span>{pdfQuestions.length}</span>
        </div>
        {!pdfQuestions.length ? <div className="review-queue-empty">No questions were extracted from an uploaded PDF.</div> : (
          <div className="saved-question-list is-sidebar">
            {pdfQuestions.map((question) => {
              const link = question.learning_object_links?.[0];
              const group = groups.find((item) => Number(item.id) === Number(link?.learning_object_group_id));
              const material = materialById.get(Number(question.material));
              const concept = group?.display_title || group?.label || group?.learning_objects?.[0]?.title || link?.learning_object_title || "No concept assigned";
              return (
                <article className="saved-question-card" key={question.id}>
                  <div className="question-review-meta">
                    <span className={`question-origin-pill is-${question.source_type || "pdf"}`}>
                      {question.source_type === "manual" ? "Manual" : question.source_type === "generated" ? "Generated" : "PDF"}
                    </span>
                    {question.thinking_order && <span className="question-thinking-pill">{question.thinking_order}</span>}
                    {question.bloom_level && !question.thinking_order && (
                      <span className="has-tip question-not-served" data-tip={NOT_SERVED_TIP} tabIndex={0}>
                        Not given to learners
                      </span>
                    )}
                    {question.category && (
                      <span className={`question-category-pill ${categoryPillClass(question.category)}`}>
                        {question.category}
                      </span>
                    )}
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
      </div>

      <div className="qt-col qt-col--tools">
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
                {group.display_title || group.label || group.learning_objects?.[0]?.title || "Untitled concept"}
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
      </div>
    </aside>
  );
}

// Every pipeline here can run for minutes, and a long run looks exactly like a
// hung one. Showing which stage is working, how far through it is, and what
// failed is the difference between "slow" and "stuck" -- so all of them report
// through this one component instead of each inventing its own.
// Problems the run handles itself -- one model attempt failing and being
// retried, a malformed reply, a rate-limit refusal -- are for whoever reads the
// server terminal, not the teacher. The run's own outcome still shows.
const TERMINAL_ONLY_EVENTS = new Set(["question_generation_attempt_failed"]);

function RunProgress({
  events,
  running,
  runningLabel,
  doneLabel,
  // Shown instead of doneLabel when the run reported problems, so a run that
  // ended without doing its job never reads as "finished".
  failedLabel = "",
  unit = "step",
  // When a caller drives several runs in sequence, the events belong to the
  // run in flight and describe only that one -- "1 of 1", finished, 100%, over
  // and over. These let the caller report the loop it is actually working
  // through, and name the thing being worked on rather than echoing whatever
  // the pipeline last happened to emit.
  index: indexOverride = null,
  total: totalOverride = null,
  current: currentOverride = "",
}) {
  // Callers keep rendering this while events exist, so a finished run would
  // otherwise leave a full-screen dialog with no way past it. Dismissal lives
  // here rather than in a prop because one caller only receives the event list,
  // not the setter that would clear it.
  const [dismissed, setDismissed] = useState(false);
  useEffect(() => {
    // A new run re-opens the dialog even if the last one was dismissed.
    if (running) setDismissed(false);
  }, [running]);

  const latest = events[events.length - 1];
  const progress = [...events].reverse().find((event) => event.data?.total);
  // Matched by shape rather than by a list of names: every pipeline reports a
  // dedicated *_failed event or the pipeline-wide "error", so a new one's
  // failures appear here without having to be registered first.
  const failures = events.filter(
    (event) =>
      !TERMINAL_ONLY_EVENTS.has(event.event_type)
      && (event.event_type === "error" || (event.event_type || "").endsWith("_failed")),
  );
  // A caller-supplied position describes the loop the teacher is waiting on;
  // the events describe only the run in flight. Prefer the caller's -- and when
  // a caller names what it is working on without counting it, respect that
  // silence rather than falling back to the run's "1 of 1", which shows a
  // finished bar for the whole of a single-item job.
  const callerReports = Boolean(currentOverride) || indexOverride !== null;
  const index = callerReports ? indexOverride : progress?.data?.index;
  const total = callerReports ? totalOverride : progress?.data?.total;
  // A caller may name what it is working on without counting it, which leaves
  // the position undefined -- so check for a real number rather than for any
  // total, or the bar reads "NaN%" beside a blank count.
  const counted = Number.isFinite(index) && Number.isFinite(total) && total > 0;
  // Indeterminate until the first counter arrives -- a bar pinned at zero
  // reads as "nothing is happening", which is the opposite of the truth. A run
  // that ends without ever being counted still ended, so it fills rather than
  // carrying on sweeping under the word "Finished".
  const percent = counted ? Math.round((index / total) * 100) : running ? null : 100;
  const currentLine = currentOverride || latest?.message || "Starting…";

  if (dismissed && !running) return null;

  const endedWithProblems = !running && failures.length > 0;
  const heading = running
    ? runningLabel
    : endedWithProblems && failedLabel ? failedLabel : doneLabel;

  return (
    <div className="run-progress-backdrop" role="presentation">
      <div
        className="run-progress-modal"
        role="dialog"
        aria-modal="true"
        aria-live="polite"
        aria-label={heading}
      >
        <div className="run-progress-head">
          <div>
            <span className={`connection-eyebrow ${endedWithProblems ? "is-problem" : ""}`.trim()}>
              {running ? "Working" : endedWithProblems ? "Needs attention" : "Finished"}
            </span>
            <h3>{heading}</h3>
          </div>
          <div className="run-progress-head-side">
            {counted ? (
              <span className="run-progress-count">
                {unit} {index} of {total}
              </span>
            ) : null}
            {/* Only once it is safe to walk away -- closing mid-run would hide
                a process the teacher cannot otherwise follow. */}
            {running ? null : (
              <button
                type="button"
                className="btn btn-secondary btn-small"
                onClick={() => setDismissed(true)}
              >
                Close
              </button>
            )}
          </div>
        </div>

        <div
          className={`publish-trace-bar${percent === null ? " is-indeterminate" : ""}`}
          role="progressbar"
          aria-valuenow={percent === null ? undefined : percent}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <span style={percent === null ? undefined : { width: `${percent}%` }} />
        </div>

        {/* The step being worked on right now -- the one thing worth reading
            while waiting. The step-by-step record is in the server terminal. */}
        <p className="run-progress-current">{currentLine}</p>
        {percent === null ? null : <span className="run-progress-percent">{percent}%</span>}

        {failures.length > 0 && (
          <ul className="publish-trace-failures">
            {failures.map((event) => (
              <li key={event.uid || event.seq}>{event.message}</li>
            ))}
          </ul>
        )}

      </div>
    </div>
  );
}

function PublishPanel({
  courseId,
  topicId,
  topic,
  groups,
  materialById,
  confirmedSourceCount,
  busyAction,
  onReviewStepChange,
  onResourcesChange,
  onCourseChange,
  onError,
  onMessage,
}) {
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

  return (
    <section className="connection-review-panel publish-review-panel" aria-labelledby="publish-panel-title">
      <div className="connection-review-heading">
        <div>
          <span className="connection-eyebrow">Final review</span>
          <h3 id="publish-panel-title">Content and questions</h3>
        </div>
        <span className="connection-source-count">
          {groups.length} concept{groups.length === 1 ? "" : "s"}
        </span>
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
                    <h4>{group.display_title || group.label || group.learning_objects[0]?.title || "Untitled concept"}</h4>
                  </div>
                  <span className={`connection-status ${isConnected ? "is-connected" : "is-single"}`}>
                    {versionSummary(group)}
                  </span>
                </header>
                {/* One block per version, each naming where it came from and
                    which objects it is made of. Every object appears exactly
                    once, under the role it actually plays -- the screen used
                    to show the concept's lead with its own text as "Standard"
                    and every other object as "Other variation", which said
                    nothing about what those objects were for and printed a
                    supplied version's wording twice. */}
                <div className="publish-version-list">
                  {VERSION_ROLES.map(({ key, label }) => {
                    const slot = group.versions?.slots?.[key];
                    if (!slot) {
                      return (
                        <section className={`publish-version-block is-${key} is-missing`} key={key}>
                          <header className="publish-version-head">
                            <span className={`version-slot-label is-${key}`}>{label}</span>
                            <small>Not generated yet</small>
                          </header>
                        </section>
                      );
                    }
                    const objects = slot.objects || [];
                    const material = materialById.get(Number(slot.material));
                    const from = slot.source === "generated"
                      ? `Generated from the Standard version · ${objects.length} segment${objects.length === 1 ? "" : "s"}`
                      : `${material?.filename || material?.title || `PDF ${slot.material}`} · ${objects.length} object${objects.length === 1 ? "" : "s"}`;
                    return (
                      <section className={`publish-version-block is-${key}`} key={key}>
                        <header className="publish-version-head">
                          <span className={`version-slot-label is-${key}`}>{label}</span>
                          <small>{from}</small>
                          {slot.stale && (
                            <span className="publish-version-stale" role="status">
                              Written before the Standard text changed
                            </span>
                          )}
                        </header>
                        <ol className="publish-version-objects">
                          {objects.map((object) => (
                            <li key={`${key}-${object.id}`}>
                              <div className="publish-item-heading">
                                <strong>{object.title}</strong>
                                {object.kind === "image" && (
                                  <span className="publish-object-kind">figure</span>
                                )}
                                {/* Deleting removes the object from the lesson,
                                    so it is offered where the object is shown,
                                    and only where the object really lives --
                                    a generated segment is not an object. */}
                                {slot.source !== "generated" && (
                                  <button
                                    type="button"
                                    className="btn btn-danger btn-small"
                                    disabled={Boolean(busyAction) || Boolean(deletingKey)}
                                    aria-label={`Delete "${object.title}"`}
                                    onClick={() => handleDeleteObject(object)}
                                  >
                                    {deletingKey === `object-${object.id}` ? "Deleting..." : "Delete"}
                                  </button>
                                )}
                              </div>
                              <FormattedLearningObjectContent
                                content={object.text || ""}
                                className="learning-object-content-text"
                              />
                            </li>
                          ))}
                        </ol>
                      </section>
                    );
                  })}
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
                            disabled={Boolean(busyAction) || Boolean(deletingKey)}
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

      <StepDock>
        <button
          type="button"
          className="btn btn-secondary"
          disabled={Boolean(busyAction) || Boolean(deletingKey)}
          onClick={() => onReviewStepChange("questions")}
        >
          Back to question pairs
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={Boolean(busyAction) || Boolean(deletingKey)}
          onClick={() => onReviewStepChange("path")}
        >
          Next: review learning path
        </button>
      </StepDock>
    </section>
  );
}

const REGROUPING_ACTION_LABELS = {
  stay: "Stays",
  move: "Move",
  separate: "Stand alone",
  unscored: "Check manually",
};

// Teacher-facing names for the stored version slots.
const VERSION_SLOT_LABELS = { simplified: "Simplified", elaborated: "Elaborated" };

// A blocking wait for work with no steps to report: scoring a handful of edited
// objects, or applying the chosen changes. Same look as the pipeline progress.
function RegroupingBusy({ title, detail }) {
  return (
    <div className="run-progress-backdrop" role="presentation">
      <div className="run-progress-modal" role="dialog" aria-modal="true" aria-live="polite" aria-label={title}>
        <div className="run-progress-head">
          <h3>{title}</h3>
        </div>
        <div className="publish-trace-bar is-indeterminate" aria-hidden="true"><span /></div>
        <p className="run-progress-current">{detail}</p>
      </div>
    </div>
  );
}

function RegroupingReview({ preview, selectedIds, busy, onToggle, onCancel, onApply }) {
  const proposals = preview.proposals || [];
  const selectable = proposals.filter((proposal) => proposal.selectable);
  const chosenCount = selectable.filter((proposal) => selectedIds.includes(proposal.learning_object_id)).length;

  return (
    <div className="run-progress-backdrop" role="presentation">
      <div
        className="run-progress-modal regrouping-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="regrouping-review-title"
      >
        <div className="run-progress-head">
          <div>
            <span className="connection-eyebrow">Edited learning objects</span>
            <h3 id="regrouping-review-title">Review grouping changes</h3>
          </div>
          <span className="run-progress-count">
            {proposals.length} edited · {selectable.length} with a proposed change
          </span>
        </div>

        {preview.published && (
          <p className="regrouping-warning" role="alert">
            This topic is published. Applying any change unpublishes it until you publish again,
            so students never see a half-updated lesson.
          </p>
        )}

        {proposals.length === 0 ? (
          <p className="run-progress-current">Nothing has been edited since it was grouped.</p>
        ) : (
          <ul className="regrouping-list">
            {proposals.map((proposal) => {
              const checked = selectedIds.includes(proposal.learning_object_id);
              const impact = proposal.impact || {};
              const removedSlots = (impact.removed_version_slots || [])
                .map((slot) => VERSION_SLOT_LABELS[slot] || slot);
              return (
                <li
                  key={proposal.learning_object_id}
                  className={`regrouping-row is-${proposal.action} ${checked ? "is-chosen" : ""}`.trim()}
                >
                  <label className="regrouping-choice">
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!proposal.selectable || busy}
                      onChange={() => onToggle(proposal.learning_object_id)}
                    />
                    <span className="sr-only">Apply the change for {proposal.title}</span>
                  </label>
                  <div className="regrouping-body">
                    <div className="regrouping-title-row">
                      <strong>{proposal.title}</strong>
                      <span className={`regrouping-pill is-${proposal.action}`}>
                        {REGROUPING_ACTION_LABELS[proposal.action] || proposal.action}
                      </span>
                    </div>
                    <small className="regrouping-source">{proposal.material_title}</small>
                    <p className="regrouping-reason">{proposal.reason}</p>
                    {proposal.selectable && (
                      <p className="regrouping-route">
                        <span>{proposal.current_group?.label || "Current concept"}</span>
                        <span aria-hidden="true"> → </span>
                        <span>
                          {proposal.action === "move"
                            ? proposal.destination_group?.label || "Matched concept"
                            : "its own concept"}
                        </span>
                      </p>
                    )}
                    {proposal.selectable && (
                      <ul className="regrouping-impact">
                        {proposal.teacher_made && (
                          <li className="is-teacher">
                            You grouped this yourself, so the change is not ticked. Tick it to overrule
                            your earlier decision.
                          </li>
                        )}
                        {impact.was_original && (
                          <li>
                            This is the Standard version of “{proposal.current_group?.label}”. That concept
                            will need a new original, and its versions reviewed again.
                          </li>
                        )}
                        {removedSlots.length > 0 && (
                          <li>
                            Removes the {removedSlots.join(" and ")} text it supplied to that concept.
                          </li>
                        )}
                        {impact.question_count > 0 && (
                          <li>
                            {impact.question_count} linked question{impact.question_count === 1 ? "" : "s"} move
                            with it.
                          </li>
                        )}
                      </ul>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        <div className="modal-actions">
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={onApply}>
            {chosenCount === 0
              ? "Keep everything as it is"
              : `Apply ${chosenCount} change${chosenCount === 1 ? "" : "s"}`}
          </button>
        </div>
      </div>
    </div>
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
  onGapsChange,
  onError,
  onMessage,
}) {
  const [resources, setResources] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState("");
  const [versionGenerationEvents, setVersionGenerationEvents] = useState([]);
  // Writing the missing versions is a series of local-model calls with nothing
  // to watch. This is the position within that walk, so the dialog can say
  // which concept is being written rather than only that something is running.
  const [missingProgress, setMissingProgress] = useState(null);
  const [filter, setFilter] = useState("all");
  // The open grouping review, and which of its proposals the teacher ticked.
  const [regroupPreview, setRegroupPreview] = useState(null);
  const [regroupSelectedIds, setRegroupSelectedIds] = useState([]);
  const [versionRemoval, setVersionRemoval] = useState(null);
  const automaticClassificationRef = useRef("");
  // The bundle controls, keyed `${object id}-${action}`, and the one to focus
  // again once a correction has come back. See `bundleControlKey`.
  const bundleControlRefs = useRef(new Map());
  const refocusAfterCorrection = useRef("");
  // The review panel itself: rendered for the whole "objects" step regardless
  // of filter or search, so it is where focus lands when a correction's own
  // control (and its row's "Move to..." fallback) is no longer rendered --
  // Move out on the last object of another PDF in a filtered/searched view
  // sends the object into a one-PDF concept the view then hides, leaving
  // neither control mounted.
  const reviewPanelRef = useRef(null);

  const materialSignature = useMemo(
    () => materials
      // Automatic content-version classification is triggered by the PDF
      // batch, not by teacher review edits. Group membership, role changes and
      // Standard replacement must never look like a newly uploaded document.
      .map((material) => (
        `${material.id}:${material.file_sha256 || ""}:${Boolean(material.generated_json?.learning_objects_confirmed)}`
      ))
      .join("|"),
    [materials],
  );
  // Compatibility with the earlier client-only guard. Accept it once and
  // migrate it so installing this fix does not itself launch one extra run.
  const legacyMaterialSignature = useMemo(
    () => materials
      .map((material) => `${material.id}:${Boolean(material.generated_json?.learning_objects_confirmed)}:${material.learning_objects.map((item) => `${item.id}:${item.group}:${item.title}:${(item.content || "").length}`).join(",")}`)
      .join("|"),
    [materials],
  );
  // A ref prevents duplicate runs while this screen stays mounted. Persisting
  // the same signature prevents navigation away and back from treating the
  // unchanged PDFs as a new batch and launching Gemma again.
  const automaticClassificationStorageKey = `mavia:version-classification:${courseId}:${topicId}`;

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

  // Grouping can place corroborated objects into one concept on its own, and a
  // placement unpublishes the topic so the published lesson never describes
  // content that changed. Nothing else announces that, so the panel says it.
  // The payload's flag is fresh; the course record the page was loaded with is
  // not, so a disagreement means grouping is what changed it.
  const payloadPublished = resources?.outline_node?.published;
  const sawPublishedRef = useRef(Boolean(topic?.published));
  useEffect(() => {
    if (payloadPublished) sawPublishedRef.current = true;
  }, [payloadPublished]);
  const unpublishedByGrouping = payloadPublished === false
    && (sawPublishedRef.current || Boolean(topic?.published));

  const groups = resources?.learning_object_groups || [];
  // The page header's Learning path button needs this, and it is only known
  // here, where the concepts are loaded.
  const matchSuggestions = resources?.match_suggestions || [];
  const allQuestionPairings = resources?.question_pairings || [];
  const gaps = contentGaps(groups, allQuestionPairings);
  useEffect(() => {
    if (resources) onGapsChange(gaps);
  }, [resources, gaps.ready, gaps.missingVersions, gaps.shortQuestions, gaps.outOfDateQuestions, gaps.unlabelledQuestions]); // eslint-disable-line react-hooks/exhaustive-deps

  // Printed questions are labelled when the Questions step opens, so every
  // question process happens on that screen. A failure stays on screen with
  // Try again, and the step cannot be left until it works.
  const [labelling, setLabelling] = useState({ running: false, error: "" });
  async function labelQuestions() {
    setLabelling({ running: true, error: "" });
    try {
      setResources(await labelTopicQuestions(courseId, topicId));
      setLabelling({ running: false, error: "" });
    } catch (err) {
      setLabelling({ running: false, error: err.message });
    }
  }
  useEffect(() => {
    if (reviewStep === "questions" && gaps.unlabelledQuestions > 0 && !labelling.running && !labelling.error) {
      labelQuestions();
    }
  }, [reviewStep, gaps.unlabelledQuestions]); // eslint-disable-line react-hooks/exhaustive-deps
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
  // Keyboard focus across a bundle correction.
  //
  // Every correction replaces the whole payload, so the row the teacher just
  // pressed in is unmounted and mounted again -- and when the object moves to
  // another concept it is mounted somewhere else entirely. A teacher on a
  // screen reader would land back at the top of the page after every press.
  // The controls therefore keep themselves in the tab order (`aria-disabled`
  // rather than `disabled`, which blurs a focused element the moment it is
  // set), and the control that was pressed is focused again once the request
  // has come back and the new payload has rendered.
  function bundleControlKey(item, action) {
    return `${item.id}-${action}`;
  }

  function registerBundleControl(key) {
    return (element) => {
      if (element) bundleControlRefs.current.set(key, element);
      else bundleControlRefs.current.delete(key);
    };
  }

  // The destinations a "Move to..." menu offers: every other concept of this
  // topic, named the way the cards name them so the two cannot disagree.
  function conceptName(group) {
    return group.display_title || group.label || group.learning_objects?.[0]?.title || "Untitled concept";
  }

  function otherConcepts(groupId) {
    return groups.filter((group) => group.id !== groupId);
  }

  // How many PDFs teach this concept. That -- not how many objects one PDF
  // chops it into -- is what "Grouped", "Standalone" and "N variations" are
  // about: the teacher is judging cross-PDF corroboration here, and a concept
  // one PDF teaches as three objects is still a single variation.
  function variationCount(group) {
    if (group.bundles) return group.bundles.length;
    return new Set((group.learning_objects || []).map((item) => item.material)).size;
  }

  const connectedGroups = groups.filter((group) => variationCount(group) > 1);
  const singletonGroups = groups.filter((group) => variationCount(group) === 1);
  const unclassifiedGroupSignature = groups
    .filter((group) => group.versions?.classification_complete === false)
    .map((group) => group.id)
    .join(",");
  const visibleGroups = groups.filter((group) => {
    if (filter === "connected" && variationCount(group) <= 1) return false;
    if (filter === "single" && variationCount(group) !== 1) return false;
    return true;
  });
  // Grouped objects edited since their grouping was decided. Counted by the
  // server without running any model, so it is cheap to show on every load.
  const regroupChangedCount = resources?.regrouping?.changed_count || 0;

  async function openRegroupingReview() {
    if (reviewStep !== "objects" || !regroupChangedCount) return;
    setBusyAction("regroup-preview");
    onError("");
    onMessage("");
    try {
      const preview = await fetchRegroupingPreview(courseId, topicId);
      setRegroupPreview(preview);
      setRegroupSelectedIds(
        (preview.proposals || [])
          .filter((proposal) => proposal.default_selected)
          .map((proposal) => proposal.learning_object_id),
      );
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  function toggleRegroupSelection(objectId) {
    setRegroupSelectedIds((current) => (
      current.includes(objectId)
        ? current.filter((id) => id !== objectId)
        : [...current, objectId]
    ));
  }

  async function applyRegroupingReview() {
    if (!regroupPreview) return;
    const chosen = regroupSelectedIds.filter((id) => (
      regroupPreview.proposals.some((proposal) => proposal.selectable && proposal.learning_object_id === id)
    ));
    setBusyAction("regroup-apply");
    onError("");
    onMessage("");
    try {
      const data = await applyRegrouping(courseId, topicId, chosen);
      setResources(data.resources);
      setRegroupPreview(null);
      setRegroupSelectedIds([]);
      const { applied = [], unpublished } = data.summary || {};
      if (unpublished || applied.length) {
        // Publication state and group membership live on the course the page
        // holds, so it has to be reloaded for the rest of the review to agree.
        onCourseChange(await fetchCourse(courseId));
      }
      onMessage(
        applied.length === 0
          ? "Grouping kept as it is. The edited learning objects are marked as reviewed."
          : `${applied.length} learning object${applied.length === 1 ? "" : "s"} regrouped.${
            unpublished ? " The topic is unpublished until you publish it again." : ""
          } Review the content versions of the affected concepts.`,
      );
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  useEffect(() => {
    if (reviewStep !== "versions") automaticClassificationRef.current = "";
  }, [reviewStep]);

  useEffect(() => {
    if (
      reviewStep !== "versions"
      || loading
      || busyAction
      || !unclassifiedGroupSignature
    ) return;
    // Keyed on the uploaded material, never on which concepts are still
    // unclassified: a concept the model fails on stays unclassified, which
    // would shrink that list, change the key and launch the identical run a
    // second time. The material signature is what a new upload changes and
    // what a classification run leaves alone, so the run fires once per batch
    // of new content and a failure reaches the teacher instead of retrying
    // itself.
    const runKey = `${topicId}:${materialSignature}`;
    let storedSignature = "";
    try {
      storedSignature = window.localStorage.getItem(automaticClassificationStorageKey) || "";
    } catch {
      // Restricted/private browser storage is optional; the in-memory guard
      // still prevents duplicate calls while this page remains mounted.
    }
    if (automaticClassificationRef.current === runKey || storedSignature === materialSignature) return;
    if (storedSignature && storedSignature === legacyMaterialSignature) {
      try {
        window.localStorage.setItem(automaticClassificationStorageKey, materialSignature);
      } catch {
        // The in-memory guard remains available if browser storage is blocked.
      }
      automaticClassificationRef.current = runKey;
      return;
    }
    automaticClassificationRef.current = runKey;
    try {
      window.localStorage.setItem(automaticClassificationStorageKey, materialSignature);
    } catch {
      // See the read above. Classification still works without persistence.
    }
    generateAllVersions();
  }, [
    reviewStep,
    loading,
    busyAction,
    topicId,
    materialSignature,
    legacyMaterialSignature,
    unclassifiedGroupSignature,
    automaticClassificationStorageKey,
  ]);

  // Every correction to an automatic bundle runs through one handler shape:
  // call, replace the payload, say what happened. Buttons only -- the teachers
  // this is built for work with a screen reader, so nothing is dragged.
  async function runBundleCorrection(item, call, describe, focusKey = "") {
    if (reviewStep !== "objects" || busyAction) return;
    if (focusKey) refocusAfterCorrection.current = focusKey;
    setBusyAction(`move-${item.id}`);
    onError("");
    onMessage("");
    try {
      const data = await call();
      setResources(data);
      onMessage(withUnpublishedNote(describe, data));
    } catch (err) {
      onError(err.message);
    } finally {
      setBusyAction("");
    }
  }

  // Runs after the corrected payload has rendered, so the control is the new
  // DOM node rather than the unmounted one. The object may have moved into
  // another concept, where the "Move out" button is gone the moment its new
  // concept holds it alone -- so the row's "Move to..." menu, which is always
  // rendered, is the fallback that keeps the teacher on the object they just
  // moved instead of at the top of the page. Under a filter or search that
  // new concept's row can itself be hidden -- a one-PDF concept Move out just
  // created is filtered out by "Grouped", for instance -- so neither control
  // exists to receive focus. The review panel landmark is the last resort:
  // it is rendered for the whole "objects" step no matter what the filter or
  // search hides, so focus never falls all the way back to the document body.
  useEffect(() => {
    if (busyAction || !refocusAfterCorrection.current) return;
    const [objectId] = refocusAfterCorrection.current.split("-");
    const target = [refocusAfterCorrection.current, `${objectId}-move-to`]
      .map((key) => bundleControlRefs.current.get(key))
      .find((element) => element && element.isConnected && !element.disabled);
    refocusAfterCorrection.current = "";
    if (target) target.focus();
    else if (reviewPanelRef.current) reviewPanelRef.current.focus();
  }, [busyAction, resources]);

  function moveObjectOutOfBundle(item) {
    return runBundleCorrection(
      item,
      () => moveObjectOut(courseId, topicId, item.id),
      `“${item.title}” is now a concept of its own.`,
      bundleControlKey(item, "move-out"),
    );
  }

  function moveObjectToGroup(item, groupId, groupLabelText) {
    return runBundleCorrection(
      item,
      () => moveObjectToConcept(courseId, topicId, item.id, groupId),
      `“${item.title}” was moved to “${groupLabelText}”.`,
      bundleControlKey(item, "move-to"),
    );
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
          ? withUnpublishedNote("Suggested learning objects were connected.", data)
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
      // This request is a teacher decision about the current PDF batch. Record
      // the batch before rendering the response so the automatic effect cannot
      // mistake the changed review state for a new upload and launch Gemma.
      automaticClassificationRef.current = `${topicId}:${materialSignature}`;
      try {
        window.localStorage.setItem(automaticClassificationStorageKey, materialSignature);
      } catch {
        // Private/restricted storage is optional; the in-memory guard remains.
      }
      setResources(data);
      onMessage(
        `Set as the ${slot.toLowerCase()} version.`
        + (data.version_assignment?.needs_review ? " The previous source now needs review." : ""),
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
        ? [{
          id: group.versions.representative_id,
          missingCount,
          // Named so the progress dialog can report the concept rather than an
          // anonymous position in a queue.
          label: group.display_title
            || group.label
            || group.learning_objects?.[0]?.title
            || "Untitled concept",
        }]
        : [];
    });
    if (!targets.length) return;
    const totalMissing = targets.reduce((count, target) => count + target.missingCount, 0);

    setBusyAction("version-generate-missing-all");
    setMissingProgress({ index: 0, total: targets.length, label: "" });
    onError("");
    onMessage(`Generating ${totalMissing} missing version${totalMissing === 1 ? "" : "s"}.`);
    let generatedCount = 0;
    const failures = [];
    // One request per concept, and a concept can fail on both of its slots, so
    // failures are reported per concept rather than as a count of errors.
    const failedConcepts = new Set();
    let latest = null;
    try {
      for (let position = 0; position < targets.length; position += 1) {
        const target = targets[position];
        setMissingProgress({
          index: position + 1,
          total: targets.length,
          label: target.label || "this concept",
        });
        try {
          const data = await generateObjectVersions(
            courseId,
            topicId,
            target.id,
          );
          // Held until the loop ends. Publishing resources per iteration made
          // the whole page re-render on every concept, which is what kept
          // throwing the teacher back to the uploaded file view.
          latest = data;
          const result = data.version_generation || {};
          generatedCount += result.generated?.length || 0;
          if (result.errors?.length) {
            failures.push(...result.errors);
            failedConcepts.add(target.label);
          }
        } catch (err) {
          failures.push({ detail: err.message });
          failedConcepts.add(target.label);
        }
      }
      if (latest) setResources(latest);
      onMessage(
        `Generated ${generatedCount} missing version${generatedCount === 1 ? "" : "s"}.`,
      );
      if (failures.length) {
        const count = failedConcepts.size;
        onError(
          `${count} concept${count === 1 ? "" : "s"} could not get ${count === 1 ? "its" : "their"} missing versions (${[...failedConcepts].join(", ")}). ${failures[0].detail || ""}`.trim(),
        );
      }
    } finally {
      setBusyAction("");
      setMissingProgress(null);
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
            `${summary.source_variant_count || 0} PDF variant${summary.source_variant_count === 1 ? " was" : "s were"} classified; ${summary.needs_review_count || 0} need teacher review. `
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

  async function keepVersion(variantId) {
    setBusyAction(`version-keep-${variantId}`);
    onError("");
    onMessage("");
    try {
      setResources(await keepVersionText(courseId, topicId, variantId));
      onMessage("Version kept. It is marked as checked against the current Standard text.");
      return true;
    } catch (err) {
      onError(err.message);
      return false;
    } finally {
      setBusyAction("");
    }
  }

  async function regenerateVersion(learningObjectId, slot) {
    const label = slot === "SIMPLIFIED" ? "Simplified" : "Elaborated";
    if (!window.confirm(`Replace this ${label} version with a newly written one? The current wording will be discarded.`)) {
      return false;
    }
    setBusyAction(`version-generate-${learningObjectId}-${slot.toLowerCase()}`);
    onError("");
    onMessage("");
    try {
      const data = await generateObjectVersions(courseId, topicId, learningObjectId, slot, { replaceStale: true });
      setResources(data);
      const result = data.version_generation || {};
      if (result.errors?.length) {
        onError(result.errors[0].detail || "Version generation failed.");
        return false;
      }
      onMessage(`Wrote a new ${label} version from the current Standard text.`);
      return true;
    } catch (err) {
      onError(err.message);
      return false;
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

  function requestVersionRemoval(entry, slot) {
    const label = slot === "SIMPLIFIED" ? "Simplified" : "Elaborated";
    setVersionRemoval({ entry, slot, label });
  }

  async function confirmVersionRemoval() {
    if (!versionRemoval) return false;
    const { entry, slot, label } = versionRemoval;
    const key = `version-delete-${slot.toLowerCase()}-${entry.id || entry.source_learning_object_id}`;
    setBusyAction(key);
    onError("");
    onMessage("");
    try {
      const data = await removeVersion(courseId, topicId, {
        slot,
        variantId: entry.id,
        sourceLearningObjectId: entry.origin === "source_pdf"
          ? entry.source_learning_object_id
          : null,
      });
      setResources(data);
      setVersionRemoval(null);
      onMessage(`${label} version removed. You can generate or assign another version.`);
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
    <div className="review-steps-frame">
    <ReviewSteps step={reviewStep} />
    <div className={`connection-review-layout ${["questions", "publish", "path"].includes(reviewStep) ? "" : "has-recommendations"}`.trim()}>
      {reviewStep === "objects" && (
      <>
      {/* The heading sits on the page, not inside the scrolling panel below
          it: it names the whole section, so it should stay put while the list
          it names is scrolled, and it should not be boxed in with the list's
          own content. */}
      <div className="connection-review-heading is-outside">
        <div>
          <span className="connection-eyebrow">Teacher review</span>
          <h3 id="connection-review-title">Related Concepts</h3>
          {(resources?.grouping_warnings || []).map((warning) => (
            <p role="alert" key={warning}>{warning}</p>
          ))}
          {unpublishedByGrouping && (
            <p role="status" className="connection-unpublished-note">
              Grouping changed, so this topic was unpublished. Republish when you are ready.
            </p>
          )}
        </div>
        <span className="connection-source-count">
          {confirmedSourceCount} confirmed source{confirmedSourceCount === 1 ? "" : "s"}
        </span>
      </div>

      <section
        className="connection-review-panel"
        aria-labelledby="connection-review-title"
        ref={reviewPanelRef}
        tabIndex={-1}
      >

      {!loading && (
        <div className={`regrouping-notice ${regroupChangedCount ? "has-changes" : ""}`.trim()}>
          <p>
            {regroupChangedCount
              ? `${regroupChangedCount} edited learning object${regroupChangedCount === 1 ? "" : "s"} may belong to a different concept now.`
              : "No grouped learning object has been edited since it was grouped."}
          </p>
          {/* Visible but disabled when there is nothing to review, so the action is
              discoverable without inviting a click that would do nothing. */}
          <button
            type="button"
            className={`btn btn-small ${regroupChangedCount ? "btn-primary" : "btn-secondary"}`}
            disabled={!regroupChangedCount || Boolean(busyAction)}
            title={regroupChangedCount ? undefined : "Enabled after you edit a grouped learning object and confirm its file again."}
            onClick={openRegroupingReview}
          >
            Review grouping changes
          </button>
        </div>
      )}

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
          </div>

          {!visibleGroups.length ? (
            <div className="connection-empty">
              {confirmedSourceCount === 0
                ? "Confirm the extracted learning objects in a lesson file before reviewing connections."
                : filter === "connected"
                  ? "No grouped concepts match this view. Open “Standalone” and move an object into the matching concept."
                  : "No learning-object groups match this filter."}
            </div>
          ) : (
            <div className="connection-group-list">
              {visibleGroups.map((group, groupIndex) => {
                const variations = variationCount(group);
                const isConnected = variations > 1;
                const groupNumber = groupIndex + 1;
                return (
                  <article className={`connection-group-card ${isConnected ? "is-connected" : ""}`} key={group.id}>
                    <header>
                      <div className="connection-group-heading-copy">
                        <span className="connection-group-number" aria-label={`Concept ${groupNumber}`}>{groupNumber}</span>
                        <h4>{group.display_title || group.label || group.learning_objects[0]?.title || "Untitled concept"}</h4>
                      </div>
                      <span className={`connection-status ${isConnected ? "is-connected" : "is-single"}`}>
                        {isConnected
                          ? `${variations} variations`
                          : "Single variation"}
                      </span>
                    </header>

                    <div className="concept-bundle-list">
                      {(group.bundles || []).map((bundle, bundleIndex) => {
                        const bundleMaterial = materialById.get(Number(bundle.material));
                        const fileName = bundleMaterial?.filename
                          || bundleMaterial?.title
                          || `PDF ${bundle.material}`;
                        return (
                          <section
                            className="concept-bundle"
                            key={bundle.material}
                            aria-label={`What ${fileName} teaches about this concept`}
                          >
                            <header className="concept-bundle-head">
                              <h5>{fileName}</h5>
                              {/* No role badge here. This step settles which
                                  objects teach the same thing; what each PDF's
                                  bundle is used for is decided in step 2. */}
                              <small>
                                {bundle.learning_objects.length} object{bundle.learning_objects.length === 1 ? "" : "s"},
                                {" "}in the order this file teaches them
                              </small>
                            </header>
                            <div className="connection-object-list">
                              {bundle.learning_objects.map((item, itemIndex) => {
                                const isImage = isImageLearningObject(item);
                                const isMissingImageDescription = isImage && (item.narration_pending ?? !item.content?.trim());
                                const moving = busyAction === `move-${item.id}`;
                                return (
                                  <div className="connection-object-row" key={item.id}>
                                    <div className="connection-object-copy">
                                      <div className="connection-object-title-row">
                                        <span className="connection-object-order">
                                          {groupNumber}.{bundleIndex + 1}.{itemIndex + 1}
                                        </span>
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
                                              ? "Narration pending: it will be written when you publish this topic."
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
                                    </div>
                                    {reviewStep === "objects" && (
                                      // Every control here is `aria-disabled`, never
                                      // `disabled`: a disabled element is blurred the moment
                                      // the flag is set and announces nothing at a bundle's
                                      // edge. Each handler guards the press instead, and the
                                      // effect above puts focus back on the control that was
                                      // pressed once the corrected payload has rendered.
                                      <div className="concept-bundle-actions">
                                        <div
                                          className="concept-bundle-action-group"
                                          role="group"
                                          aria-label={`Which concept "${item.title}" belongs to`}
                                        >
                                          <span className="concept-bundle-action-label" aria-hidden="true">Wrong concept?</span>
                                          {group.learning_objects.length > 1 && (
                                            <button
                                              type="button"
                                              className="btn btn-secondary btn-small"
                                              ref={registerBundleControl(bundleControlKey(item, "move-out"))}
                                              aria-disabled={Boolean(busyAction)}
                                              aria-label={`Give "${item.title}" a concept of its own, separate from the rest of this one`}
                                              onClick={() => moveObjectOutOfBundle(item)}
                                            >
                                              {moving ? "Moving..." : "Give it its own concept"}
                                            </button>
                                          )}
                                          <label className="concept-bundle-move-to">
                                            <span className="sr-only">Move "{item.title}" into another concept that already exists</span>
                                            <select
                                              value=""
                                              ref={registerBundleControl(bundleControlKey(item, "move-to"))}
                                              aria-disabled={Boolean(busyAction) || otherConcepts(group.id).length === 0}
                                              onChange={(event) => {
                                                const target = otherConcepts(group.id).find(
                                                  (candidate) => String(candidate.id) === event.target.value,
                                                );
                                                event.target.value = "";
                                                if (busyAction || !target) return;
                                                moveObjectToGroup(item, target.id, conceptName(target));
                                              }}
                                            >
                                              <option value="">Move into another concept...</option>
                                              {otherConcepts(group.id).map((candidate) => (
                                                <option key={candidate.id} value={candidate.id}>
                                                  {conceptName(candidate)}
                                                </option>
                                              ))}
                                            </select>
                                          </label>
                                        </div>
                                      </div>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          </section>
                        );
                      })}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
          </div>
        </>
      )}

      </section>
      </>
      )}
      {busyAction === "regroup-preview" && (
        <RegroupingBusy
          title="Checking edited learning objects"
          detail={`Comparing ${regroupChangedCount} edited learning object${regroupChangedCount === 1 ? "" : "s"} against every concept in this topic…`}
        />
      )}
      {busyAction === "regroup-apply" && (
        <RegroupingBusy title="Applying grouping changes" detail="Updating concepts and the questions linked to them…" />
      )}
      {regroupPreview && busyAction !== "regroup-apply" && (
        <RegroupingReview
          preview={regroupPreview}
          selectedIds={regroupSelectedIds}
          busy={Boolean(busyAction)}
          onToggle={toggleRegroupSelection}
          onCancel={() => {
            setRegroupPreview(null);
            setRegroupSelectedIds([]);
          }}
          onApply={applyRegroupingReview}
        />
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
          missingProgress={missingProgress}
          onEditVersion={saveVersionText}
          onKeepVersion={keepVersion}
          onRegenerateVersion={regenerateVersion}
          onRemoveVersion={requestVersionRemoval}
        />
      )}
      {reviewStep === "questions" && (
        <>
          <ReviewQueuePanel
            questionPairings={allQuestionPairings}
            unlabelledCount={gaps.unlabelledQuestions}
            labelling={labelling}
            onRetryLabelling={labelQuestions}
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
            manualPanel={(
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
            )}
          />
        </>
      )}
      {reviewStep === "publish" && (
        <PublishPanel
          courseId={courseId}
          topicId={topicId}
          topic={topic}
          groups={groups}
          materialById={materialById}
          confirmedSourceCount={confirmedSourceCount}
          busyAction={busyAction}
          onReviewStepChange={onReviewStepChange}
          onResourcesChange={setResources}
          onCourseChange={onCourseChange}
          onError={onError}
          onMessage={onMessage}
        />
      )}
      {reviewStep === "path" && (
        <LearningPathReviewPanel
          courseId={courseId}
          topicId={topicId}
          topic={topic}
          confirmedSourceCount={confirmedSourceCount}
          busyAction={busyAction}
          onReviewStepChange={onReviewStepChange}
          onResourcesChange={setResources}
          onCourseChange={onCourseChange}
          onError={onError}
          onMessage={onMessage}
        />
      )}
      {versionRemoval && createPortal(
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !busyAction) setVersionRemoval(null);
          }}
        >
          <div
            className="modal-card delete-material-modal"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="remove-version-title"
            aria-describedby="remove-version-description"
          >
            <div className="delete-modal-heading">
              <span className="delete-modal-icon" aria-hidden="true">!</span>
              <div>
                <h3 id="remove-version-title">Remove {versionRemoval.label} version?</h3>
                <p id="remove-version-description">This adaptive slot will become empty.</p>
              </div>
            </div>
            <div className="delete-material-summary">
              <strong>{versionRemoval.label}</strong>
              <span>
                {versionRemoval.entry.origin === "source_pdf"
                  ? "The PDF's original learning content will remain. Only its version role will be removed."
                  : "The generated wording will be deleted. You can generate another version later."}
              </span>
            </div>
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-secondary"
                disabled={Boolean(busyAction)}
                onClick={() => setVersionRemoval(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={Boolean(busyAction)}
                onClick={confirmVersionRemoval}
              >
                {busyAction ? "Removing..." : "Remove version"}
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
    </div>
  );
}

// Step 5. The path is derived from the content, so it cannot be reviewed until
// the content is settled -- and publishing is what turns the *reviewed* path
// into audio, which is why the publish button lives here and not a step
// earlier.
function LearningPathReviewPanel({
  courseId,
  topicId,
  topic,
  confirmedSourceCount,
  busyAction,
  onReviewStepChange,
  onResourcesChange,
  onCourseChange,
  onError,
  onMessage,
}) {
  const [pathData, setPathData] = useState(null);
  const [loadingPath, setLoadingPath] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [publishing, setPublishing] = useState(false);
  const [publishEvents, setPublishEvents] = useState([]);
  const [showPublished, setShowPublished] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadPath() {
      setLoadingPath(true);
      setLoadError("");
      try {
        const data = await fetchTopicLearningPath(topicId);
        if (!cancelled) setPathData(data);
      } catch (err) {
        // Shown by this panel's own banner, with Retry; the page-wide error
        // banner would say it twice and outlive a successful retry.
        if (!cancelled) setLoadError(err.message);
      } finally {
        if (!cancelled) setLoadingPath(false);
      }
    }

    loadPath();
    return () => {
      cancelled = true;
    };
  }, [topicId, reloadKey]);

  // Publishing narrates images, settles versions and synthesises audio, each of
  // which calls a local model. The request only starts the run; progress
  // arrives by polling the run's events, so the teacher sees which stage is
  // working rather than a spinner for several minutes.
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
    // Every event seen so far. Problems are reported while the run works, so
    // the last poll alone would miss most of them.
    const seen = [];
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
        seen.push(...incoming);
        setPublishEvents((current) => [...current, ...incoming]);
      }

      const status = payload.run?.status;
      if (status === "finished" || status === "failed") {
        setPublishing(false);
        const summary = seen.find((event) => event.event_type === "publish_finished")?.data?.summary;
        // The run can end normally while the topic stays unpublished -- it did
        // all its steps and some of them found problems. That outcome is its
        // own event, and it must never be reported as "Published."
        const unpublished = seen.some((event) => event.event_type === "publish_failed");
        if (status === "failed" || unpublished) {
          const staleConcepts = seen.filter((event) => event.event_type === "versions_failed").length;
          onError(
            staleConcepts
              ? `Not published. ${staleConcepts} concept${staleConcepts === 1 ? " has" : "s have"} a Simplified or Elaborated version to check — the Standard text changed after it was written. Open Content versions (step 2) to keep, edit or regenerate ${staleConcepts === 1 ? "it" : "them"}, then publish again.`
              : "Not published. The problems are listed in the publish window — resolve them, then publish again.",
          );
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
        if (!(status === "failed" || unpublished)) {
          // The published dialog replaces the progress window on success; a
          // failed run keeps it, since it lists the problems to resolve.
          setPublishEvents([]);
          setShowPublished(true);
        }
        try {
          onResourcesChange(await fetchLearningResources(courseId, topicId));
          // Whether the topic is published lives on the course, not on the
          // resources -- so without this the button still read "Publish
          // course" after a successful publish, until the page was reloaded.
          onCourseChange(await fetchCourse(courseId));
        } catch {
          // The run is what matters; a stale panel is recoverable by reloading.
        }
        return;
      }

      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }

  const paths = pathData?.paths || [];

  return (
    <section className="connection-review-panel" aria-labelledby="path-review-panel-title">
      <div className="connection-review-heading">
        <div>
          <span className="connection-eyebrow">Final review</span>
          <h3 id="path-review-panel-title">Learning path</h3>
          <p>
            The order this topic would be taught in, derived from the content. Review it,
            then publish to generate the lesson audio.
          </p>
        </div>
        <span className="connection-source-count">
          {/* One path now covers every file, so counting paths would always
              say "1". What the teacher wants is how much it covers. */}
          {paths[0]?.diagnostics?.concept_count ?? 0} concept
          {(paths[0]?.diagnostics?.concept_count ?? 0) === 1 ? "" : "s"}
          {" from "}
          {paths[0]?.diagnostics?.material_count ?? 0} lesson file
          {(paths[0]?.diagnostics?.material_count ?? 0) === 1 ? "" : "s"}
        </span>
      </div>

      {loadingPath && !pathData && <p className="muted-text">Deriving the path…</p>}

      {pathData?.problems?.length > 0 && (
        <div className="error-banner">
          <strong>Some lesson files have no usable order.</strong>
          <ul>
            {pathData.problems.map((problem) => (
              <li key={problem.material_id}>
                {problem.material_title}: {problem.detail}
              </li>
            ))}
          </ul>
        </div>
      )}

      {!loadingPath && loadError && !pathData && (
        <div className="error-banner" role="alert">
          Couldn't load the learning path: {loadError}{" "}
          <button type="button" className="btn btn-small btn-secondary" onClick={() => setReloadKey((key) => key + 1)}>
            Retry
          </button>
        </div>
      )}

      {!loadingPath && !loadError && !paths.length && (
        <div className="review-queue-empty">
          No path yet. Each step is a concept, so confirm the learning objects in
          your lesson files first — grouping is what turns them into concepts.
        </div>
      )}

      {paths.map((path) => (
        <MaterialPath
          key={path.topic_id ?? path.material_id}
          path={path}
          topicId={topicId}
          editable
          onPathData={setPathData}
        />
      ))}

      {showPublished && (
        <PublishedDialog
          topicId={topicId}
          topicTitle={topic?.title || ""}
          onClose={() => setShowPublished(false)}
        />
      )}

      <StepDock>
        <button
          type="button"
          className="btn btn-secondary"
          disabled={publishing || Boolean(busyAction)}
          onClick={() => onReviewStepChange("publish")}
        >
          Back to content and questions
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
            {publishing ? "Publishing..." : topic?.published ? "Republish subtopic" : "Publish subtopic"}
          </button>
        </div>
      </StepDock>

      {(publishing || publishEvents.length > 0) && (
        <RunProgress
          events={publishEvents}
          running={publishing}
          runningLabel="Publishing"
          doneLabel="Published"
          failedLabel="Not published"
        />
      )}
    </section>
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
  const [selectedId, setSelectedId] = useState(material.learning_objects[0]?.id || null);
  const [busyAction, setBusyAction] = useState("");
  const [activeTab, setActiveTab] = useState("content");
  const [showAudioWarning, setShowAudioWarning] = useState(false);
  const [showDeleteMaterialConfirm, setShowDeleteMaterialConfirm] = useState(false);
  // What confirming this edited PDF again would change in the approved PDFs,
  // and which of those changes the teacher keeps out.
  const [approvedChanges, setApprovedChanges] = useState(null);
  const [keptIds, setKeptIds] = useState([]);
  const [learningObjectToDelete, setLearningObjectToDelete] = useState(null);

  const generatedJson = material.generated_json || {};
  const isAssessmentDocument = isQuestionMaterial(material);
  const learningObjectsConfirmed = Boolean(generatedJson.learning_objects_confirmed);
  const lessonPlaylist = generatedJson.lesson_playlist || [];
  const lessonAudioGenerated = Boolean(generatedJson.lesson_audio_generated);
  const audioCount = lessonPlaylist.filter((item) => item.audio_url).length;
  // Confirmation locks the extracted list. Downstream grouping, versions,
  // questions and audio all depend on these rows, so the material must not
  // silently return to an editable draft after approval.
  const canEditLearningObjects = !learningObjectsConfirmed;
  const selectedObject = material.learning_objects.find((item) => item.id === selectedId);
  const imagesMissingDescription = material.learning_objects.filter(
    (item) => isImageLearningObject(item) && !item.content?.trim(),
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

  async function confirmObjects(keep = null) {
    setBusyAction("confirm");
    onError("");
    onMessage("");
    try {
      const updatedCourse = await confirmLearningObjects(courseId, material.id, keep);
      if (updatedCourse.approved_changes) {
        setApprovedChanges(updatedCourse.approved_changes);
        setKeptIds([]);
        return;
      }
      setApprovedChanges(null);
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
    if (learningObjectsConfirmed) return;
    const imageObject = imagesMissingDescription[0];
    if (!imageObject) return;
    setShowAudioWarning(false);
    setActiveTab("content");
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
      {figureNarrationNotice(material.figure_narration) && (
        <p role="status" className="connection-unpublished-note">
          {figureNarrationNotice(material.figure_narration)}
        </p>
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
                {learningObjectsConfirmed ? "Confirmed" : "Needs review"}
              </span>
              {!learningObjectsConfirmed && (
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
                  const isMissingImageDescription = isImage && (item.narration_pending ?? !item.content?.trim());
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
                                    ? `Narration pending for learning object ${index + 1}: it will be written when you publish this topic.`
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
                    onClick={() => confirmObjects()}
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

      {approvedChanges && (
        <div className="modal-backdrop" role="presentation">
          <div
            className="modal-card reconfirm-review-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby={`reconfirm-review-${material.id}`}
          >
            <h3 id={`reconfirm-review-${material.id}`}>
              Confirming this file changes {approvedChanges.length} approved concept
              {approvedChanges.length === 1 ? "" : "s"}
            </h3>
            <p>
              Your edits change what this file teaches, so these parts of other, already
              approved files would move. Choose what happens to each.
            </p>
            <ul className="reconfirm-change-list">
              {approvedChanges.map((change) => {
                const kept = keptIds.includes(change.learning_object_id);
                return (
                  <li key={change.learning_object_id}>
                    <div>
                      <strong>{change.title}</strong>
                      <small>{change.material_title}</small>
                      <p>
                        Now in {change.from_label}. Would move to {change.to_label}.
                      </p>
                    </div>
                    <div className="reconfirm-change-choice" role="radiogroup" aria-label={`What happens to ${change.title}`}>
                      <label>
                        <input
                          type="radio"
                          name={`reconfirm-${change.learning_object_id}`}
                          checked={!kept}
                          onChange={() => setKeptIds((ids) => ids.filter((id) => id !== change.learning_object_id))}
                        />
                        Apply the change
                      </label>
                      <label>
                        <input
                          type="radio"
                          name={`reconfirm-${change.learning_object_id}`}
                          checked={kept}
                          onChange={() => setKeptIds((ids) => [...ids, change.learning_object_id])}
                        />
                        Keep as is
                      </label>
                    </div>
                  </li>
                );
              })}
            </ul>
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-secondary"
                disabled={busyAction === "confirm"}
                onClick={() => setApprovedChanges(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={busyAction === "confirm"}
                onClick={() => confirmObjects(keptIds)}
              >
                {busyAction === "confirm" ? "Confirming..." : "Confirm file"}
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

// A PDF with many figures narrates only the first few during upload; the
// rest are narrated when the topic is published. Say so, with the count,
// while any are still waiting.
// Opens the learning path step. It stays unavailable until every concept has
// its three versions and at least one question, and says what is missing.
function LearningPathButton({ gaps, onOpen }) {
  const ready = Boolean(gaps?.ready);
  let tip = "Prepare the content first: open Review Connections to check the versions and questions.";
  if (gaps && !ready) {
    const parts = [];
    if (gaps.missingVersions) {
      parts.push(`${gaps.missingVersions} concept${gaps.missingVersions === 1 ? " needs" : "s need"} a Simplified or Elaborated version`);
    }
    if (gaps.shortQuestions) {
      parts.push(`${gaps.shortQuestions} concept${gaps.shortQuestions === 1 ? " has" : "s have"} fewer than ${gaps.minimumText}`);
    }
    if (gaps.unlabelledQuestions) {
      parts.push(`${gaps.unlabelledQuestions} printed question${gaps.unlabelledQuestions === 1 ? " needs" : "s need"} labelling in the Questions step`);
    }
    if (gaps.outOfDateQuestions) {
      parts.push(`${gaps.outOfDateQuestions} concept${gaps.outOfDateQuestions === 1 ? " has" : "s have"} questions to check`);
    }
    tip = parts.length
      ? `Prepare the content first: ${parts.join(", and ")}.`
      : "Prepare the content first: there are no concepts yet.";
  }
  return (
    <span className={ready ? undefined : "has-tip"} data-tip={ready ? undefined : tip}>
      <button
        type="button"
        className="btn btn-primary btn-small"
        aria-disabled={!ready}
        aria-describedby={ready ? undefined : "learning-path-tip"}
        onClick={() => ready && onOpen()}
      >
        Learning path
      </button>
      {!ready && <span id="learning-path-tip" className="sr-only">{tip}</span>}
    </span>
  );
}

function figureNarrationNotice(status) {
  if (!status || !status.pending) return "";
  const pending = `${status.pending} figure${status.pending === 1 ? "" : "s"}`;
  return `${pending} still need an audio narration. `
    + "They will be narrated when you publish this topic, which can take a few minutes.";
}

// Every figure is narrated during upload, about 30 seconds each.
const UPLOAD_NARRATION_NOTICE = "Processing this PDF. Every figure gets an audio narration (about 30 seconds each), "
  + "so a PDF with more than 5 figures can take several minutes. Please keep this page open.";

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
  // Null until the review view has loaded the concepts once.
  const [contentGapsState, setContentGapsState] = useState(null);

  // Only a genuine change of URL should move the teacher. This used to run on
  // every re-render caused by refreshed resources, so generating versions --
  // which refreshes once per concept -- kept throwing the teacher out of the
  // review flow and back onto the uploaded file named in `?material=`.
  const navigationTarget = `${courseId}:${topicId}:${uploadedMaterialId || ""}`;
  const lastNavigationTarget = useRef(navigationTarget);
  useEffect(() => {
    if (lastNavigationTarget.current === navigationTarget) return;
    lastNavigationTarget.current = navigationTarget;
    setActiveSource(uploadedMaterialId || "connections");
  }, [navigationTarget, uploadedMaterialId]);

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
            <img
              className="lesson-sidebar-brand-mark"
              src="/android-chrome-192x192.png"
              alt=""
              aria-hidden="true"
            />
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
          <div className="topic-detail-nav">
            <Link to={`/courses/${courseId}`} className="btn btn-secondary btn-small">
              Back to hierarchy
            </Link>
            <LearningPathButton
              gaps={contentGapsState}
              onOpen={() => {
                setActiveSource("connections");
                setConnectionReviewStep("path");
              }}
            />
          </div>
          <div className="topic-detail-header">
            <div>
              <h2>{topic.title}</h2>
              <p className="muted-text">
                Selected module/topic: {selectedModule?.title || topic.title} / {topic.title}
              </p>
            </div>
            {!(activeSource === "connections" && connectionReviewStep !== "objects") && (
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
        {uploading && (
          <p role="status" className="connection-unpublished-note">{UPLOAD_NARRATION_NOTICE}</p>
        )}

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
            onGapsChange={setContentGapsState}
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
