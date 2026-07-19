import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Background, Controls, MarkerType, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  confirmModuleConceptDag,
  createModuleConceptEdge,
  deleteModuleConceptEdge,
  fetchModuleConceptDag,
  generateModuleConceptDag,
  updateModuleConceptEdge,
} from "../api";

function buildLevels(nodes, edges) {
  const nodeIds = new Set(nodes.map((node) => node.id));
  const incoming = new Map(nodes.map((node) => [node.id, []]));
  const outgoing = new Map(nodes.map((node) => [node.id, []]));

  edges
    .filter((edge) => edge.validation_status === "approved" || edge.validation_status === "pending")
    .forEach((edge) => {
      if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return;
      incoming.get(edge.target).push(edge.source);
      outgoing.get(edge.source).push(edge.target);
    });

  const levels = new Map(nodes.map((node) => [node.id, 0]));
  const queue = nodes.filter((node) => !incoming.get(node.id)?.length).map((node) => node.id);
  const seen = new Set();

  while (queue.length) {
    const current = queue.shift();
    if (seen.has(current)) continue;
    seen.add(current);
    const nextLevel = (levels.get(current) || 0) + 1;
    outgoing.get(current)?.forEach((target) => {
      levels.set(target, Math.max(levels.get(target) || 0, nextLevel));
      queue.push(target);
    });
  }

  return levels;
}

function statusLabel(value) {
  return value ? value[0].toUpperCase() + value.slice(1) : "Unknown";
}

export default function ModuleLearnerPathPage() {
  const { courseId, moduleId } = useParams();
  const [dag, setDag] = useState(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState(null);
  const [manualEdge, setManualEdge] = useState({ source: "", target: "" });
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [generationSummary, setGenerationSummary] = useState(null);

  async function loadDag() {
    const data = await fetchModuleConceptDag(courseId, moduleId);
    setDag(data);
    setSelectedEdgeId((current) => {
      if (current && data.edges.some((edge) => edge.id === current)) return current;
      return data.edges[0]?.id || null;
    });
  }

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await fetchModuleConceptDag(courseId, moduleId);
        if (!cancelled) {
          setDag(data);
          setSelectedEdgeId(data.edges[0]?.id || null);
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
  }, [courseId, moduleId]);

  const conceptById = useMemo(() => {
    return new Map((dag?.nodes || []).map((node) => [node.id, node]));
  }, [dag?.nodes]);

  const flowNodes = useMemo(() => {
    if (!dag) return [];
    const levels = buildLevels(dag.nodes, dag.edges);
    const levelCounts = new Map();
    return dag.nodes.map((node) => {
      const level = levels.get(node.id) || 0;
      const index = levelCounts.get(level) || 0;
      levelCounts.set(level, index + 1);
      return {
        id: String(node.id),
        position: { x: level * 300, y: index * 145 },
        draggable: false,
        data: {
          label: (
            <div className="concept-node">
              <strong>{node.title}</strong>
              <span>{node.source_count} source{node.source_count === 1 ? "" : "s"}</span>
            </div>
          ),
        },
        className: `concept-flow-node concept-${node.validation_status}`,
      };
    });
  }, [dag]);

  const flowEdges = useMemo(() => {
    if (!dag) return [];
    return dag.edges
      .filter((edge) => conceptById.has(edge.source) && conceptById.has(edge.target))
      .map((edge) => ({
        id: String(edge.id),
        source: String(edge.source),
        target: String(edge.target),
        animated: edge.validation_status === "pending",
        label: edge.is_manual ? "manual" : statusLabel(edge.validation_status),
        markerEnd: { type: MarkerType.ArrowClosed },
        className: `concept-flow-edge edge-${edge.validation_status} ${edge.is_manual ? "edge-manual" : ""}`,
        data: edge,
      }));
  }, [dag, conceptById]);

  const selectedEdge = (dag?.edges || []).find((edge) => edge.id === selectedEdgeId);

  async function runAction(label, action) {
    setBusy(label);
    setError("");
    setMessage("");
    try {
      const result = await action();
      if (result?.nodes && result?.edges) {
        setDag(result);
      } else {
        await loadDag();
      }
      return result;
    } catch (err) {
      setError(err.message);
      return null;
    } finally {
      setBusy("");
    }
  }

  async function handleGenerate() {
    const result = await runAction("generate", () => generateModuleConceptDag(courseId, moduleId));
    if (!result) return;
    setGenerationSummary(result.summary || null);
    const summary = result.summary || {};
    const changedEdges = (summary.edges_created || 0) + (summary.edges_updated || 0);
    if (changedEdges > 0) {
      setMessage("Learner path generated. Review pending edges before confirming.");
    } else {
      setMessage("");
    }
  }

  async function updateEdge(edge, validationStatus) {
    const result = await runAction(`edge-${edge.id}`, () =>
      updateModuleConceptEdge(courseId, moduleId, edge.id, { validation_status: validationStatus }),
    );
    if (result) setMessage(`Edge marked ${validationStatus}.`);
  }

  async function removeEdge(edge) {
    const result = await runAction(`delete-${edge.id}`, () => deleteModuleConceptEdge(courseId, moduleId, edge.id));
    if (result) setMessage("Manual edge deleted.");
  }

  async function addManualEdge(event) {
    event.preventDefault();
    if (!manualEdge.source || !manualEdge.target) return;
    const result = await runAction("manual-edge", () =>
      createModuleConceptEdge(courseId, moduleId, {
        source: Number(manualEdge.source),
        target: Number(manualEdge.target),
      }),
    );
    if (result) {
      setManualEdge({ source: "", target: "" });
      setMessage("Manual edge added.");
    }
  }

  async function confirmDag() {
    const result = await runAction("confirm", () => confirmModuleConceptDag(courseId, moduleId));
    if (result) setMessage("Learner path confirmed.");
  }

  if (!dag && error) {
    return (
      <section className="card">
        <Link to={`/courses/${courseId}`} style={{ color: "var(--muted)" }}>
          Back to course
        </Link>
        <div className="error-banner">{error}</div>
      </section>
    );
  }

  if (!dag) return <div className="empty-state">Loading learner path...</div>;

  const pendingEdges = dag.edges.filter((edge) => edge.validation_status === "pending").length;
  const approvedEdges = dag.edges.filter((edge) => edge.validation_status === "approved").length;
  const learningObjects = dag.nodes || [];
  const generatedEdgeChanges = generationSummary
    ? (generationSummary.edges_created || 0) + (generationSummary.edges_updated || 0)
    : null;

  return (
    <section className="learner-path-page">
      <div className="dag-reference-header">
        <div className="dag-title-block">
          <Link to={`/courses/${courseId}`} className="dag-back-link">
            Back to hierarchy
          </Link>
          <h2>{dag.module.title}</h2>
          <p>Module learner path. Confirmed learning objects are the DAG nodes.</p>
        </div>
        <div className="dag-header-actions">
          <button className="btn btn-primary dag-generate-button" type="button" disabled={Boolean(busy)} onClick={handleGenerate}>
            {busy === "generate" ? "Generating..." : dag.edges.length ? "Regenerate" : "Generate learner path"}
          </button>
          <button
            className="btn btn-secondary"
            type="button"
            disabled={Boolean(busy) || pendingEdges > 0 || !dag.is_acyclic}
            onClick={confirmDag}
          >
            {busy === "confirm" ? "Confirming..." : dag.confirmed ? "Confirmed" : "Confirm path"}
          </button>
        </div>
      </div>

      {error && <div className="dag-alert dag-alert-error">{error}</div>}
      {message && <div className="dag-alert dag-alert-success">{message}</div>}
      {generationSummary && generatedEdgeChanges === 0 && (
        <div className="dag-alert dag-alert-info">
          No edges met the learner-path rules. {generationSummary.learning_objects_considered || 0} learning objects were considered,
          {` ${generationSummary.candidate_pairs_evaluated || 0} candidate pairs were evaluated, `}
          {generationSummary.edges_skipped_below_threshold || 0} were below threshold, and{" "}
          {generationSummary.edges_skipped_for_insufficient_direction || 0} lacked prerequisite direction evidence.
        </div>
      )}
      {!dag.is_acyclic && <div className="dag-alert dag-alert-error">This graph contains a cycle and cannot be confirmed.</div>}
      {!dag.edges.length && (
        <div className="dag-alert dag-alert-info">
          No concept edges yet. Disconnected concepts are allowed until a real prerequisite relation exists.
        </div>
      )}
      {dag.state?.invalidation_reason && !dag.confirmed && (
        <div className="dag-alert dag-alert-info">{dag.state.invalidation_reason}</div>
      )}

      <div className="learner-path-layout">
        <div className={`dag-canvas-panel ${dag.edges.length ? "" : "is-empty-edge-set"}`}>
          <ReactFlow
            nodes={flowNodes}
            edges={flowEdges}
            fitView
            nodesDraggable={false}
            nodesConnectable={false}
            onEdgeClick={(_, edge) => setSelectedEdgeId(Number(edge.id))}
          >
            <Background />
            <Controls />
          </ReactFlow>
        </div>

        <aside className="dag-panel dag-sidebar">
          <div className="dag-panel-title-row">
            <div>
              <h3>Learning Objects</h3>
              <span className="edge-count">{learningObjects.length} nodes</span>
            </div>
          </div>

          <div className="concept-review-list">
            {!learningObjects.length ? (
              <p className="muted-text">No confirmed learning objects yet.</p>
            ) : (
              learningObjects.map((node) => (
                <div key={node.id} className="concept-review-item">
                  <strong>{node.title}</strong>
                  <span>{node.material_title}</span>
                </div>
              ))
            )}
          </div>

          <div className="dag-panel-title-row">
            <div>
              <h3>Edges</h3>
              <span className="edge-count">{approvedEdges} approved / {pendingEdges} pending</span>
            </div>
          </div>

          <form className="manual-edge-form" onSubmit={addManualEdge}>
            <select
              value={manualEdge.source}
              disabled={Boolean(busy)}
              onChange={(event) => setManualEdge((current) => ({ ...current, source: event.target.value }))}
              aria-label="Manual edge source"
            >
              <option value="">Source concept</option>
              {dag.nodes.map((node) => (
                <option key={node.id} value={node.id}>{node.title}</option>
              ))}
            </select>
            <select
              value={manualEdge.target}
              disabled={Boolean(busy)}
              onChange={(event) => setManualEdge((current) => ({ ...current, target: event.target.value }))}
              aria-label="Manual edge target"
            >
              <option value="">Target concept</option>
              {dag.nodes.map((node) => (
                <option key={node.id} value={node.id}>{node.title}</option>
              ))}
            </select>
            <button className="btn btn-secondary btn-small" type="submit" disabled={Boolean(busy) || !manualEdge.source || !manualEdge.target}>
              Add edge
            </button>
          </form>

          <div className="edge-list">
            {dag.edges.map((edge) => (
              <button
                key={edge.id}
                className={`edge-list-item ${selectedEdgeId === edge.id ? "selected" : ""}`}
                type="button"
                onClick={() => setSelectedEdgeId(edge.id)}
              >
                <span>{conceptById.get(edge.source)?.title || edge.source}</span>
                <span>{conceptById.get(edge.target)?.title || edge.target}</span>
                <span className={`status-pill status-${edge.validation_status}`}>{statusLabel(edge.validation_status)}</span>
              </button>
            ))}
          </div>

          {selectedEdge ? (
            <div className="edge-detail">
              <div className="selected-edge-route">
                <span>{conceptById.get(selectedEdge.source)?.title}</span>
                <strong>-&gt;</strong>
                <span>{conceptById.get(selectedEdge.target)?.title}</span>
              </div>
              <div className="edge-detail-row"><span>Score</span><strong>{Number(selectedEdge.score || 0).toFixed(2)}</strong></div>
              <div className="edge-detail-row"><span>Semantic</span><strong>{selectedEdge.semantic_similarity == null ? "Manual" : Number(selectedEdge.semantic_similarity).toFixed(2)}</strong></div>
              <div className="edge-detail-row"><span>Dependency cue</span><strong>{selectedEdge.dependency_cue_score == null ? "Manual" : Number(selectedEdge.dependency_cue_score).toFixed(2)}</strong></div>
              <div className="edge-detail-row"><span>Source order</span><strong>{selectedEdge.source_order_score == null ? "Manual" : Number(selectedEdge.source_order_score).toFixed(2)}</strong></div>
              <div className="edge-detail-row"><span>Title overlap</span><strong>{selectedEdge.title_overlap_score == null ? "Manual" : Number(selectedEdge.title_overlap_score).toFixed(2)}</strong></div>
              <div className="edge-explanation">
                <span>Explanation</span>
                <p>{selectedEdge.explanation || "No explanation stored."}</p>
              </div>
              <div className="generated-item-actions">
                <button className="btn btn-primary btn-small" type="button" disabled={Boolean(busy)} onClick={() => updateEdge(selectedEdge, "approved")}>
                  Approve
                </button>
                <button className="btn btn-secondary btn-small" type="button" disabled={Boolean(busy)} onClick={() => updateEdge(selectedEdge, "rejected")}>
                  Reject
                </button>
                {selectedEdge.is_manual && (
                  <button className="btn btn-danger btn-small" type="button" disabled={Boolean(busy)} onClick={() => removeEdge(selectedEdge)}>
                    Delete manual
                  </button>
                )}
              </div>
            </div>
          ) : (
            <div className="dag-detail-empty">Select an edge to review scores and provenance.</div>
          )}
        </aside>
      </div>
    </section>
  );
}
