import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { addPathLink, decidePathLink, fetchTopicLearningPath } from "../api";

// A step nothing depends on and that depends on nothing is not part of the
// graph at all. It still gets taught, but its position is document order rather
// than anything the criteria found -- which is the first thing to look at when
// a path looks wrong.
export function findFloatingSteps(steps) {
  const depended = new Set();
  for (const step of steps) {
    for (const id of step.prerequisite_ids || []) depended.add(id);
  }
  return steps
    .filter(
      (step) =>
        (step.prerequisite_count || 0) === 0 &&
        !depended.has(step.learning_object_id),
    )
    .map((step) => step.learning_object_id);
}

// The concept map, top to bottom, in teaching order. A run of steps that sit
// under lesson headings becomes one row of branches (one column per heading, in
// the order the headings are first taught); a step under no heading stands on
// its own. Exported so the layout can be checked without rendering.
export function buildConceptMap(steps) {
  const ordered = [...steps].sort((a, b) => a.position - b.position);
  const blocks = [];
  let run = null;
  for (const step of ordered) {
    const heading = (step.branch || "").trim();
    if (!heading) {
      run = null;
      blocks.push({ kind: "single", step });
      continue;
    }
    if (!run) {
      run = { kind: "branches", columns: [] };
      blocks.push(run);
    }
    let column = run.columns.find((item) => item.heading.toLowerCase() === heading.toLowerCase());
    if (!column) {
      column = { heading, steps: [] };
      run.columns.push(column);
    }
    column.steps.push(step);
  }
  return blocks;
}

const BRANCH_TONES = 5;

// One colour per lesson heading, in the order headings are first taught, so a
// concept reads as the same branch in the list, the summary strip and the map.
function branchToneMap(steps) {
  const tones = new Map();
  for (const step of [...steps].sort((a, b) => a.position - b.position)) {
    const key = (step.branch || "").trim().toLowerCase();
    if (key && !tones.has(key)) tones.set(key, (tones.size % BRANCH_TONES) + 1);
  }
  return tones;
}

function toneOf(tones, step) {
  return tones.get((step.branch || "").trim().toLowerCase()) || "plain";
}

function MapCard({ step, tone, role, onSelect }) {
  return (
    <button
      type="button"
      className={`cm-card tone-${tone} ${role}`.trim()}
      aria-pressed={role === "is-selected"}
      onClick={() => onSelect(step.concept_id)}
    >
      <span className="cm-card-title">
        <span className="cm-card-position">{step.position}</span>
        {step.title || "Untitled concept"}
      </span>
      <span className="cm-card-text">{step.content}</span>
    </button>
  );
}

// The learning path drawn like a concept map: subtopic at the top, the lesson's
// headings as branches, concepts in teaching order. Lines show the teaching
// flow; what must be learned first is shown by selecting a box rather than
// drawn, because a comparison needing twelve concepts would bury the map in
// arrows.
function ConceptMap({ path }) {
  const steps = path.steps || [];
  const blocks = useMemo(() => buildConceptMap(steps), [steps]);
  const tones = useMemo(() => branchToneMap(steps), [steps]);
  const [selected, setSelected] = useState(null);

  const byConcept = useMemo(() => new Map(steps.map((step) => [step.concept_id, step])), [steps]);
  const needs = useMemo(() => {
    const map = new Map(steps.map((step) => [step.concept_id, new Set()]));
    for (const step of steps) {
      for (const link of step.prerequisites || []) map.get(step.concept_id).add(link.concept_id);
    }
    return map;
  }, [steps]);
  const leads = useMemo(() => {
    const map = new Map(steps.map((step) => [step.concept_id, new Set()]));
    for (const [dependent, prerequisites] of needs) {
      for (const prerequisite of prerequisites) map.get(prerequisite)?.add(dependent);
    }
    return map;
  }, [steps, needs]);

  useEffect(() => {
    if (selected !== null && !byConcept.has(selected)) setSelected(null);
  }, [byConcept, selected]);

  if (!steps.length) return <p className="muted-text">This topic has no teaching steps yet.</p>;

  const needsFirst = selected !== null ? needs.get(selected) : new Set();
  const leadsTo = selected !== null ? leads.get(selected) : new Set();
  const roleOf = (conceptId) => {
    if (selected === null) return "";
    if (conceptId === selected) return "is-selected";
    if (needsFirst.has(conceptId)) return "is-needed";
    if (leadsTo.has(conceptId)) return "is-leads";
    return "is-dimmed";
  };
  const select = (conceptId) => setSelected((current) => (current === conceptId ? null : conceptId));
  const chosen = selected !== null ? byConcept.get(selected) : null;

  return (
    <div className="cm">
      <div className="cm-toolbar" aria-live="polite">
        {chosen ? (
          <>
            <span>
              Before <strong>{chosen.title}</strong>, a student must learn{" "}
              <span className="cm-key is-needed">{needsFirst.size} concept{needsFirst.size === 1 ? "" : "s"}</span>.{" "}
              <span className="cm-key is-leads">{leadsTo.size}</span> build on it.
            </span>
            <button type="button" className="btn btn-small btn-secondary" onClick={() => setSelected(null)}>
              Clear
            </button>
          </>
        ) : (
          <span className="muted-text">
            Select a concept to see what must be learned before it, and what builds on it.
          </span>
        )}
      </div>

      <div className="cm-scroll">
        <div className="cm-canvas">
          <div className="cm-root">{path.topic_title || "Learning path"}</div>
          {blocks.map((block, index) => {
            if (block.kind === "single") {
              return (
                <div className="cm-block" key={`single-${block.step.concept_id}`}>
                  <span className="cm-link" aria-hidden="true" />
                  <div className="cm-row">
                    <MapCard step={block.step} tone="plain" role={roleOf(block.step.concept_id)} onSelect={select} />
                  </div>
                </div>
              );
            }
            return (
              <div className="cm-block" key={`branches-${index}`}>
                <span className="cm-link" aria-hidden="true" />
                <div className="cm-branches" style={{ "--cols": block.columns.length }}>
                  {block.columns.map((column) => {
                    const tone = tones.get(column.heading.toLowerCase()) || "plain";
                    return (
                      <div className="cm-column" key={column.heading}>
                        <div className={`cm-heading tone-${tone}`}>{column.heading}</div>
                        <div className="cm-stack">
                          {column.steps.map((step) => (
                            <MapCard
                              key={step.concept_id}
                              step={step}
                              tone={tone}
                              role={roleOf(step.concept_id)}
                              onSelect={select}
                            />
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// "What must be learned before this concept?" -- the teacher's controls for one
// step. Changes save at once and the preview re-orders, but students keep the
// published path until the topic is published again.
function LearnFirstPanel({ step, stepsById, busy, onAdd, onDecide }) {
  const current = step.prerequisites || [];
  const suggestions = step.suggestions || [];
  const taken = new Set([step.concept_id, ...current.map((link) => link.concept_id)]);
  const choices = [...stepsById.values()]
    .filter((other) => !taken.has(other.concept_id))
    .sort((a, b) => a.position - b.position);

  return (
    <section className="lf" aria-label={`What must be learned before ${step.title}`}>
      <header className="lf-head">
        <h5>What must a student learn before this?</h5>
        <span className="lf-count">
          {current.length === 0 ? "Nothing yet" : `${current.length} concept${current.length === 1 ? "" : "s"}`}
        </span>
      </header>

      {current.length > 0 && (
        <ol className="lf-list">
          {current.map((link) => {
            const other = stepsById.get(link.concept_id);
            return (
              <li key={link.link_id} className="lf-item">
                <span className="lf-position">{other?.position ?? "–"}</span>
                <span className="lf-title">{link.title}</span>
                <button
                  type="button"
                  className="lf-remove"
                  disabled={busy}
                  onClick={() => {
                    if (window.confirm(`Remove “${link.title}” from what must be learned before “${step.title}”? It won't be suggested again.`)) {
                      onDecide(link.link_id, "rejected");
                    }
                  }}
                >
                  Remove
                </button>
              </li>
            );
          })}
        </ol>
      )}

      <label className="lf-add" htmlFor={`learn-first-${step.concept_id}`}>
        <span>Add a concept that must come first</span>
        <select
          id={`learn-first-${step.concept_id}`}
          value=""
          disabled={busy || choices.length === 0}
          onChange={(event) => {
            if (event.target.value) onAdd(Number(event.target.value), step.concept_id);
          }}
        >
          <option value="">Choose a concept…</option>
          {choices.map((other) => (
            <option key={other.concept_id} value={other.concept_id}>
              {other.position}. {other.title}
            </option>
          ))}
        </select>
      </label>

      {suggestions.length > 0 && (
        <details className="lf-suggestions">
          <summary>
            The system thinks {suggestions.length === 1 ? "this concept" : `these ${suggestions.length} concepts`} may
            also need to come before “{step.title}” — review {suggestions.length === 1 ? "it" : "them"}
          </summary>
          <ul>
            {suggestions.map((link) => {
              const other = stepsById.get(link.concept_id);
              return (
                <li key={link.link_id} className="lf-suggestion">
                  <div className="lf-suggestion-head">
                    <span className="lf-position">{other?.position ?? "–"}</span>
                    <strong>{link.title}</strong>
                    {other?.branch && <span className="lf-branch">{other.branch}</span>}
                    {link.cross_section && (
                      <span className="lf-flag" title="The two concepts are under different lesson headings. In a hand-check, such suggestions were usually wrong.">
                        different section
                      </span>
                    )}
                  </div>
                  {other?.content && <p className="lf-suggestion-text">{other.content}</p>}
                  <div className="lf-suggestion-actions">
                    <span>Must “{link.title}” be learned before “{step.title}”?</span>
                    <button type="button" className="btn btn-small btn-primary" disabled={busy} onClick={() => onDecide(link.link_id, "approved")}>
                      Yes, learn it first
                    </button>
                    <button type="button" className="btn btn-small btn-secondary" disabled={busy} onClick={() => onDecide(link.link_id, "rejected")}>
                      No
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        </details>
      )}
    </section>
  );
}

function PathStep({ step, tone, floating, editor }) {
  const moved = step.position - 1 !== step.source_order;
  const prerequisites = (step.prerequisites || []).map((link) => link.title);

  // The box's stroke says the concept's status, so it reads before the chips do.
  return (
    <li
      className={`path-step ${floating ? "is-floating" : ""} ${moved ? "is-moved" : ""}`.trim()}
      id={`path-step-${step.concept_id}`}
    >
      <span className={`path-step-position tone-${tone}`}>{step.position}</span>
      <div className="path-step-body">
        <div className="path-step-head">
          <strong>{step.title || "Untitled"}</strong>
          {step.branch && <span className={`path-branch tone-${tone}`}>{step.branch}</span>}
          {step.kind === "image" && <span className="path-chip is-image">Figure</span>}
          {floating && (
            <span className="path-chip is-floating" title="Nothing must come before it, and nothing builds on it.">
              Not linked
            </span>
          )}
          {moved && (
            <span className="path-chip is-moved" title="A learn-first link moved it from where the lesson files place it.">
              Moved from #{step.source_order + 1}
            </span>
          )}
        </div>
        {step.content && <p className="path-step-text">{step.content}</p>}
        {editor || (
          <p className="path-step-needs">
            {prerequisites.length
              ? <>Learn first: <em>{prerequisites.join(", ")}</em></>
              : <span className="muted-text">Nothing must be learned before this.</span>}
          </p>
        )}
      </div>
    </li>
  );
}

// The path's counts, each with a swatch of the stroke that marks it on the
// concept boxes below -- so the line doubles as the key to those strokes.
function PathStats({ steps, floating, linkCount }) {
  const moved = steps.filter((step) => step.position - 1 !== step.source_order).length;

  return (
    <p className="ps-caption">
      <span><b>{linkCount}</b> learn-first link{linkCount === 1 ? "" : "s"}</span>
      {floating.size > 0 && (
        <span><i className="ps-swatch is-floating" aria-hidden="true" /><b>{floating.size}</b> not linked to any other</span>
      )}
      {moved > 0 && (
        <span><i className="ps-swatch is-moved" aria-hidden="true" /><b>{moved}</b> moved from the lesson files' order</span>
      )}
    </p>
  );
}

// Exported so the topic review flow can show the same path display inline as
// its own step, rather than keeping a second copy in sync with this one.
export function MaterialPath({ path, topicId = null, editable = false, onPathData = null }) {
  const [view, setView] = useState("list");
  const [linkBusy, setLinkBusy] = useState(false);
  const [linkError, setLinkError] = useState("");
  const steps = path.steps || [];
  const stepsById = useMemo(() => new Map(steps.map((step) => [step.concept_id, step])), [steps]);
  const tones = useMemo(() => branchToneMap(steps), [steps]);
  const orderedSteps = useMemo(() => [...steps].sort((a, b) => a.position - b.position), [steps]);
  const linkCount = steps.reduce((count, step) => count + (step.prerequisites || []).length, 0);
  const floating = useMemo(
    () => (linkCount === 0 ? new Set() : new Set(findFloatingSteps(steps))),
    [steps, linkCount],
  );

  async function changeLinks(action) {
    setLinkBusy(true);
    setLinkError("");
    try {
      const data = await action();
      if (onPathData) onPathData(data);
    } catch (error) {
      setLinkError(error.message);
    } finally {
      setLinkBusy(false);
    }
  }

  const editorFor = (step) => (editable && topicId ? (
    <LearnFirstPanel
      step={step}
      stepsById={stepsById}
      busy={linkBusy}
      onAdd={(prerequisiteId, dependentId) => changeLinks(() => addPathLink(topicId, prerequisiteId, dependentId))}
      onDecide={(linkId, status) => changeLinks(() => decidePathLink(topicId, linkId, status))}
    />
  ) : null);

  return (
    <section className="path-material">
      <header className="path-material-head">
        <h3>{path.topic_title || path.material_title || "Learning path"}</h3>
        <div className="path-view-toggle" role="group" aria-label="View">
          <button
            type="button"
            className={`btn btn-small ${view === "list" ? "btn-primary" : "btn-secondary"}`}
            aria-pressed={view === "list"}
            onClick={() => setView("list")}
          >
            List
          </button>
          <button
            type="button"
            className={`btn btn-small ${view === "graph" ? "btn-primary" : "btn-secondary"}`}
            aria-pressed={view === "graph"}
            onClick={() => setView("graph")}
          >
            Graph
          </button>
        </div>
      </header>

      {steps.length > 0 && <PathStats steps={steps} floating={floating} linkCount={linkCount} />}

      {linkCount === 0 && steps.length > 0 && (
        <p className="muted-text path-ordering-note">
          Ordered as your lesson files present it. Nothing has to be learned before anything
          else yet{editable ? " — add a concept that must come first on any step below" : ""}.
        </p>
      )}

      {editable && path.diagnostics?.changed_since_publish && (
        <p className="path-changed-note" role="status">
          You changed what must be learned first after the last publish. Students still follow
          the published path until you publish again.
        </p>
      )}

      {linkError && <p className="path-link-error" role="alert">{linkError}</p>}

      {view === "graph" && <ConceptMap path={path} />}

      {view === "list" && !steps.length ? (
        <p className="muted-text">This topic has no teaching steps yet.</p>
      ) : view === "list" ? (
        // Teaching order, top to bottom.
        <ol className="path-step-list">
          {orderedSteps.map((step) => (
            <PathStep
              key={step.concept_id}
              step={step}
              tone={toneOf(tones, step)}
              floating={floating.has(step.learning_object_id)}
              editor={editorFor(step)}
            />
          ))}
        </ol>
      ) : null}
    </section>
  );
}

export default function LearningPathPage() {
  const { courseId, topicId } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await fetchTopicLearningPath(topicId));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [topicId]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="learning-path-page">
      <div className="learning-path-heading">
        <div>
          <span className="connection-eyebrow">Learning path</span>
          <h2>{data?.topic?.title || "Learning path"}</h2>
          <p className="muted-text">
            One path for the whole topic. Each step is a concept, assembled from every
            uploaded file that teaches it.
          </p>
        </div>
        <div className="learning-path-actions">
          <button type="button" className="btn btn-secondary" disabled={loading} onClick={load}>
            {loading ? "Loading..." : "Refresh"}
          </button>
          <Link className="btn btn-secondary" to={`/courses/${courseId}/topics/${topicId}`}>
            Back to topic
          </Link>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {loading && !data && <p className="muted-text">Deriving the path…</p>}

      {data?.problems?.length > 0 && (
        <div className="alert alert-error">
          <strong>Some lesson files have no usable order.</strong>
          <ul>
            {data.problems.map((problem) => (
              <li key={problem.material_id}>
                {problem.material_title}: {problem.detail}
              </li>
            ))}
          </ul>
        </div>
      )}

      {data?.paths?.length === 0 && !loading && (
        <p className="muted-text">
          No path yet. Each step is a concept, so confirm the learning objects in
          your lesson files first — grouping is what turns them into concepts.
        </p>
      )}

      {(data?.paths || []).map((path) => (
        <MaterialPath key={path.material_id} path={path} />
      ))}
    </div>
  );
}
