import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Background, Controls, Handle, Position, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { decideCoursePathLink, fetchCourseLearningPath, restoreCoursePathLinks } from "../api";
import ConfirmDialog from "../learning-path/ConfirmDialog";
import CourseArrowDialog from "../learning-path/CourseArrowDialog";
import UndoBar from "../learning-path/UndoBar";
import { buildCourseGraph, learningOrder } from "../learning-path/coursePathModel";
import "../learning-path/pathGraph.css";
import "../learning-path/coursePath.css";

function TopicNode({ data }) {
  const { topic, empty } = data;
  return (
    <div className={`cp-topic${empty ? " empty" : ""}`} title={topic.title}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <span className="cp-topic-position">{topic.position + 1}</span>
      <span className="cp-topic-title">{topic.title}</span>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}

function LabelNode({ data }) {
  return <div className="pg-label">{data.text}</div>;
}

const NODE_TYPES = { topic: TopicNode, label: LabelNode };

export default function CoursePathPage() {
  const { courseId } = useParams();
  const [path, setPath] = useState(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [undo, setUndo] = useState(null);

  useEffect(() => {
    fetchCourseLearningPath(courseId)
      .then(setPath)
      .catch((err) => setError(err.message || "Could not load the course path."));
  }, [courseId]);

  const graph = useMemo(() => (path ? buildCourseGraph(path, selected) : { nodes: [], edges: [] }), [path, selected]);
  const order = useMemo(() => (path ? learningOrder(path) : []), [path]);
  const arrow = path?.arrows.find((item) => `${item.from_topic}-${item.to_topic}` === selected) || null;
  const topicTitle = (id) => path?.topics.find((topic) => topic.id === id)?.title || "";

  const decide = useCallback(async (link, status) => {
    setBusy(true);
    try {
      const result = await decideCoursePathLink(courseId, link.id, status);
      setPath(result);
      setConfirm(null);
      setUndo({
        records: result.undo,
        message: `${status === "approved" ? "Approved" : "Removed"}: ${link.prerequisite.title} → ${link.dependent.title}.`,
      });
    } catch (err) {
      setConfirm((current) => current && { ...current, error: err.message || "Could not save." });
    } finally {
      setBusy(false);
    }
  }, [courseId]);

  const handleUndo = useCallback(async () => {
    if (!undo) return;
    setBusy(true);
    try {
      setPath(await restoreCoursePathLinks(courseId, undo.records));
      setUndo(null);
    } catch (err) {
      setUndo((current) => current && { ...current, error: err.message || "Could not undo." });
    } finally {
      setBusy(false);
    }
  }, [courseId, undo]);
  const closeUndo = useCallback(() => setUndo(null), []);

  const ask = (link, status) => setConfirm({
    title: status === "approved" ? "Approve this link?" : "Remove this link?",
    message: `${link.prerequisite.title} (${topicTitle(link.prerequisite.topic_id)}) before ${link.dependent.title} (${topicTitle(link.dependent.topic_id)}).`,
    actions: [{ label: status === "approved" ? "Approve" : "Remove", primary: true, onClick: () => decide(link, status) }],
  });

  if (error) return <div className="error-banner">{error}</div>;
  if (!path) return <p className="muted-text">Loading the course path…</p>;

  return (
    <>
      <section className="card">
        <Link to={`/courses/${courseId}`} style={{ color: "var(--muted)" }}>Back to course</Link>
        <h2>Course path: {path.course.title}</h2>
        <p className="muted-text">
          Topics flow top to bottom in your outline order. An arrow means a concept in one topic builds on a
          concept in another. Green follows your outline, yellow is a suggestion, red contradicts your outline.
          Below, the learning order shows where each step can send a learner back to an earlier topic.
        </p>
      </section>
      {/* One scroller for the graph and the list: the shell itself never scrolls (refresh.css). */}
      <div className="cp-page">
      <div className="cp-layout">
        <div className="pg-canvas">
          <ReactFlow
            nodes={graph.nodes}
            edges={graph.edges}
            nodeTypes={NODE_TYPES}
            nodesDraggable={false}
            onEdgeClick={(_, edge) => setSelected(edge.id)}
            onPaneClick={() => setSelected(null)}
            fitView
          >
            <Background />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      </div>
      <section className="card cp-order" aria-labelledby="cp-order-title">
        <h3 id="cp-order-title">Learning order</h3>
        {order.length === 0 && <p className="muted-text">No topic has lessons yet.</p>}
        <ol className="cp-order-topics">
          {order.map(({ topic, published, steps }) => (
            <li key={topic.id}>
              <h4>
                {topic.title}
                {!published && <span className="cp-unpublished">not published yet</span>}
              </h4>
              <ol className="cp-order-steps">
                {steps.map((step) => (
                  <li key={step.concept_id}>
                    <span className="cp-step-title">{step.title}</span>
                    {step.revisit.length > 0 && (
                      <div className="cp-step-links">
                        <span className="cp-step-label">May revisit:</span>
                        {step.revisit.map((link) => (
                          <span key={link.id} className={`cp-chip ${link.status}`} title={link.reason}>
                            {link.prerequisite.title} — {topicTitle(link.prerequisite.topic_id)}
                            <button type="button" className="btn btn-small btn-secondary" onClick={() => ask(link, "rejected")}>Remove</button>
                          </span>
                        ))}
                      </div>
                    )}
                    {step.suggestions.length > 0 && (
                      <div className="cp-step-links">
                        <span className="cp-step-label">Might build on:</span>
                        {step.suggestions.map((link) => (
                          <span key={link.id} className="cp-chip pending" title={link.reason}>
                            {link.prerequisite.title} — {topicTitle(link.prerequisite.topic_id)}
                            <button type="button" className="btn btn-small btn-primary" onClick={() => ask(link, "approved")}>Approve</button>
                            <button type="button" className="btn btn-small btn-secondary" onClick={() => ask(link, "rejected")}>Dismiss</button>
                          </span>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ol>
            </li>
          ))}
        </ol>
      </section>
      </div>
      {arrow && (
        <CourseArrowDialog
          arrow={arrow}
          topicTitle={topicTitle}
          onDecide={ask}
          onClose={() => setSelected(null)}
          paused={Boolean(confirm) || busy}
        />
      )}
      {confirm && (
        <ConfirmDialog
          title={confirm.title}
          message={confirm.message}
          actions={confirm.actions}
          error={confirm.error}
          busy={busy}
          onCancel={() => setConfirm(null)}
        />
      )}
      {undo && <UndoBar message={undo.message} error={undo.error} busy={busy} onUndo={handleUndo} onClose={closeUndo} />}
    </>
  );
}
