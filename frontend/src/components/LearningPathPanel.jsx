import { useCallback, useEffect, useMemo, useState } from "react";

import {
  createPrerequisiteEdge,
  deletePrerequisiteEdge,
  fetchTopicLearningPath,
  rebuildMaterialLearningPath,
} from "../api";
import LearningPathGraph from "./LearningPathGraph";

// Mirrors learning_path.models.PrerequisiteEdge.Signal.
const SIGNAL_LABELS = {
  chunk_continuation: "continues",
  title_reference: "names it",
  definition_scope: "defines first",
  section_reference: "section names it",
  term_cooccurrence: "shared terms",
  teacher_authored: "you added",
};

function signalLabel(edge) {
  return SIGNAL_LABELS[edge.signal] || edge.signal;
}

function evidenceSummary(edge) {
  const evidence = edge.evidence || {};
  if (evidence.term) return `matched "${evidence.term}"`;
  if (evidence.defines) return `defines "${evidence.defines}"`;
  if (evidence.base_title) return `part ${evidence.part} of ${evidence.of}`;
  if (evidence.distinctive_shared_terms?.length) {
    return `shares ${evidence.distinctive_shared_terms.slice(0, 3).join(", ")}`;
  }
  if (edge.source === "teacher") return "your decision";
  return "";
}

/** The prerequisites of one lesson, with the controls to change them. */
function StepEditor({ step, incoming, options, titleById, busy, onAdd, onRemove }) {
  const [adding, setAdding] = useState(false);
  const [chosen, setChosen] = useState("");

  return (
    <div className="learning-path-step-body">
      <strong>{step.title || "(untitled)"}</strong>
      <span className="learning-path-step-meta">
        step {step.position} · layer {step.dag_depth} · was #{step.source_order + 1} in the PDF
      </span>

      {incoming.length === 0 ? (
        <p className="learning-path-no-prereq">Starts here — nothing must be taught first.</p>
      ) : (
        <ul className="learning-path-prereqs">
          {incoming.map((edge) => (
            <li key={edge.id}>
              <span className="learning-path-prereq-title">
                after {titleById.get(edge.prerequisite_id) || edge.prerequisite_title}
              </span>
              <span
                className={`learning-path-signal${
                  edge.source === "teacher" ? " is-teacher" : ""
                }`}
              >
                {signalLabel(edge)}
                {evidenceSummary(edge) ? ` — ${evidenceSummary(edge)}` : ""}
              </span>
              <button
                type="button"
                className="btn btn-small btn-danger"
                disabled={busy}
                onClick={() => onRemove(edge.id)}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      {adding ? (
        <div className="learning-path-add-row">
          <label className="sr-only" htmlFor={`prereq-${step.learning_object_id}`}>
            Lesson that must come first
          </label>
          <select
            id={`prereq-${step.learning_object_id}`}
            value={chosen}
            disabled={busy}
            onChange={(event) => setChosen(event.target.value)}
          >
            <option value="">Choose what must come first…</option>
            {options.map((candidate) => (
              <option
                key={candidate.learning_object_id}
                value={candidate.learning_object_id}
              >
                {candidate.position}. {candidate.title || "(untitled)"}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy || !chosen}
            onClick={async () => {
              await onAdd(Number(chosen), step.learning_object_id);
              setAdding(false);
              setChosen("");
            }}
          >
            Add
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={busy}
            onClick={() => {
              setAdding(false);
              setChosen("");
            }}
          >
            Cancel
          </button>
        </div>
      ) : (
        <button
          type="button"
          className="btn btn-small btn-secondary"
          disabled={busy}
          onClick={() => setAdding(true)}
        >
          Add a prerequisite
        </button>
      )}
    </div>
  );
}

/**
 * Step 3 of topic review: the teacher checks the order every student will be
 * taught in, before the course is published.
 *
 * The order is derived, not authored, so editing works on the *reasons* rather
 * than on the sequence itself: remove a prerequisite the system got wrong, or
 * add one it missed, and the path re-sorts. That keeps what the teacher sees
 * and what the algorithm produces as the same thing.
 */
export default function LearningPathPanel({
  topicId,
  topic,
  busyAction,
  onReviewStepChange,
  onError,
  onMessage,
}) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [working, setWorking] = useState(false);
  const [verified, setVerified] = useState(false);
  const [view, setView] = useState("graph");
  const [selected, setSelected] = useState({});

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      setData(await fetchTopicLearningPath(topicId));
    } catch (error) {
      // Keep the failure visible here as well as in the page banner. Without
      // this the panel falls through to its empty state and tells the teacher
      // there is no lesson material, which is the wrong diagnosis.
      setLoadError(error.message);
      onError?.(error.message);
    } finally {
      setLoading(false);
    }
  }, [topicId, onError]);

  useEffect(() => {
    load();
  }, [load]);

  // Any change to the graph invalidates a previous verification.
  const applyPath = useCallback((updated) => {
    setVerified(false);
    setData((current) => {
      if (!current) return current;
      return {
        ...current,
        paths: current.paths.map((path) =>
          path.material_id === updated.material_id ? updated : path
        ),
      };
    });
  }, []);

  async function handleRemoveEdge(edgeId) {
    setWorking(true);
    try {
      applyPath(await deletePrerequisiteEdge(edgeId));
      onMessage?.("Prerequisite removed. The order has been recalculated.");
    } catch (error) {
      onError?.(error.message);
    } finally {
      setWorking(false);
    }
  }

  async function handleAddEdge(prerequisiteId, dependentId) {
    setWorking(true);
    try {
      applyPath(await createPrerequisiteEdge(prerequisiteId, dependentId));
      onMessage?.("Prerequisite added. The order has been recalculated.");
    } catch (error) {
      onError?.(error.message);
    } finally {
      setWorking(false);
    }
  }

  async function handleRebuild(materialId) {
    setWorking(true);
    try {
      applyPath(await rebuildMaterialLearningPath(materialId));
      onMessage?.(
        "Learning path re-derived from the lesson text. Prerequisites you added were kept."
      );
    } catch (error) {
      onError?.(error.message);
    } finally {
      setWorking(false);
    }
  }

  const busy = working || Boolean(busyAction);
  const paths = data?.paths || [];
  const problems = data?.problems || [];
  const totalSteps = useMemo(
    () => paths.reduce((sum, path) => sum + path.steps.length, 0),
    [paths]
  );

  return (
    <section className="connection-review-panel" aria-labelledby="learning-path-panel-title">
      <div className="panel-heading">
        <div>
          <h3 id="learning-path-panel-title">Learning path</h3>
          <p className="panel-subtitle">
            The order every student will be taught in, worked out from the lesson text
            rather than copied from the PDF&apos;s page order. Check it before publishing.
          </p>
        </div>
        <span>{totalSteps} steps</span>
      </div>

      <div className="review-step-indicator has-four-steps" aria-label="Review progress">
        <span>1</span>
        <div aria-hidden="true" />
        <span>2</span>
        <div aria-hidden="true" />
        <span className="is-active">3</span>
        <div aria-hidden="true" />
        <span>4</span>
        <strong>Learning path</strong>
      </div>

      {loading && <div className="review-queue-empty">Working out the learning path…</div>}

      {!loading && loadError && (
        <div className="learning-path-problem" role="alert">
          <strong>The learning path could not be loaded.</strong>
          <p>{loadError}</p>
          <button type="button" className="btn btn-secondary" onClick={load}>
            Try again
          </button>
        </div>
      )}

      {!loading && problems.length > 0 && (
        <div className="learning-path-problem" role="alert">
          <strong>This path could not be produced.</strong>
          {problems.map((problem) => (
            <p key={problem.material_id}>
              {problem.material_title}: {problem.detail}
            </p>
          ))}
        </div>
      )}

      {!loading && !loadError && !paths.length && !problems.length && (
        <div className="review-queue-empty">
          No lesson material has been processed for this topic yet.
        </div>
      )}

      {!loading && paths.length > 0 && (
        <div className="learning-path-legend">
          <span>
            <i className="lp-key lp-key-layer" aria-hidden="true" />
            Each band is one <strong>layer</strong> — everything in it can be taught in any
            order once the layers above are done.
          </span>
          <span>
            <i className="lp-key lp-key-strong" aria-hidden="true" />
            A thicker arrow means a more strongly evidenced prerequisite.
          </span>
          <span>
            <i className="lp-key lp-key-teacher" aria-hidden="true" />
            Dashed arrows are ones you added.
          </span>
        </div>
      )}

      {!loading &&
        paths.map((path) => {
          const titleById = new Map(
            path.steps.map((step) => [step.learning_object_id, step.title])
          );
          const edgesByDependent = new Map();
          (path.edges || []).forEach((edge) => {
            const list = edgesByDependent.get(edge.dependent_id) || [];
            list.push(edge);
            edgesByDependent.set(edge.dependent_id, list);
          });

          const optionsFor = (step) => {
            const incoming = edgesByDependent.get(step.learning_object_id) || [];
            return path.steps.filter(
              (candidate) =>
                candidate.learning_object_id !== step.learning_object_id &&
                !incoming.some(
                  (edge) => edge.prerequisite_id === candidate.learning_object_id
                )
            );
          };

          const selectedId = selected[path.material_id] ?? null;
          const selectedStep = path.steps.find(
            (step) => step.learning_object_id === selectedId
          );
          const layerCount = new Set(path.steps.map((step) => step.dag_depth)).size;

          return (
            <article className="learning-path-material" key={path.material_id}>
              <header className="learning-path-material-header">
                <div>
                  <h4>{path.material_title}</h4>
                  <p className="learning-path-diagnostics">
                    {path.diagnostics.node_count} lessons · {layerCount} layer
                    {layerCount === 1 ? "" : "s"} · {path.diagnostics.edge_count} prerequisites
                    {path.diagnostics.matches_source_order ? (
                      <em> · same order as the PDF</em>
                    ) : (
                      <em>
                        {" "}
                        · {path.diagnostics.displaced_object_count} of{" "}
                        {path.diagnostics.node_count} moved from the PDF&apos;s order
                      </em>
                    )}
                  </p>
                </div>
                <div className="learning-path-header-actions">
                  <div className="learning-path-view-toggle" role="group" aria-label="View">
                    <button
                      type="button"
                      className={view === "graph" ? "is-active" : ""}
                      aria-pressed={view === "graph"}
                      onClick={() => setView("graph")}
                    >
                      Graph
                    </button>
                    <button
                      type="button"
                      className={view === "list" ? "is-active" : ""}
                      aria-pressed={view === "list"}
                      onClick={() => setView("list")}
                    >
                      List
                    </button>
                  </div>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={busy}
                    onClick={() => handleRebuild(path.material_id)}
                  >
                    Re-derive from text
                  </button>
                </div>
              </header>

              {view === "graph" ? (
                <>
                  <LearningPathGraph
                    steps={path.steps}
                    edges={path.edges || []}
                    selectedId={selectedId}
                    onSelect={(id) =>
                      setSelected((current) => ({ ...current, [path.material_id]: id }))
                    }
                  />
                  <div className="learning-path-selection">
                    {selectedStep ? (
                      <div className="learning-path-step-main">
                        <span className="learning-path-step-position">
                          {selectedStep.position}
                        </span>
                        <StepEditor
                          step={selectedStep}
                          incoming={edgesByDependent.get(selectedId) || []}
                          options={optionsFor(selectedStep)}
                          titleById={titleById}
                          busy={busy}
                          onAdd={handleAddEdge}
                          onRemove={handleRemoveEdge}
                        />
                      </div>
                    ) : (
                      <p className="learning-path-selection-hint">
                        Select a lesson in the graph to see and edit what must be taught
                        before it.
                      </p>
                    )}
                  </div>
                </>
              ) : (
                <ol className="learning-path-steps">
                  {path.steps.map((step) => (
                    <li className="learning-path-step" key={step.learning_object_id}>
                      <div className="learning-path-step-main">
                        <span className="learning-path-step-position">{step.position}</span>
                        <StepEditor
                          step={step}
                          incoming={edgesByDependent.get(step.learning_object_id) || []}
                          options={optionsFor(step)}
                          titleById={titleById}
                          busy={busy}
                          onAdd={handleAddEdge}
                          onRemove={handleRemoveEdge}
                        />
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </article>
          );
        })}

      {!loading && paths.length > 0 && (
        <label className="learning-path-verify">
          <input
            type="checkbox"
            checked={verified}
            disabled={busy}
            onChange={(event) => setVerified(event.target.checked)}
          />
          <span>
            I have checked this order and it makes sense for {topic?.title || "this topic"}.
          </span>
        </label>
      )}

      <div className="review-step-actions-row">
        <button
          type="button"
          className="btn btn-secondary"
          disabled={busy}
          onClick={() => onReviewStepChange("questions")}
        >
          Back to question pairs
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || !verified || !paths.length}
          onClick={() => onReviewStepChange("publish")}
        >
          Next step: Publish
        </button>
      </div>
    </section>
  );
}
