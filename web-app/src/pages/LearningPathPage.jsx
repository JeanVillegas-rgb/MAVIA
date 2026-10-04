import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { addPathLink, decidePathLink, fetchTopicLearningPath, movePathLink, restorePathLinks } from "../api";
import ConceptDetails from "../learning-path/ConceptDetails";
import ConfirmDialog from "../learning-path/ConfirmDialog";
import { classifyDrop, conceptsToReview } from "../learning-path/graphModel";
import PathGraph from "../learning-path/PathGraph";
import UndoBar from "../learning-path/UndoBar";

// Exported so the topic review flow shows the same path display inline as its
// own step, rather than keeping a second copy in sync with this one.
export function MaterialPath({ path, topicId = null, editable = false, onPathData = null }) {
  const steps = path.steps || [];
  const canEdit = editable && topicId !== null;
  const [selectedId, setSelectedId] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [confirmError, setConfirmError] = useState("");
  const [busy, setBusy] = useState(false);
  const [undo, setUndo] = useState(null);

  const selected = steps.find((step) => step.concept_id === selectedId) || null;
  const titleOf = (id) => steps.find((step) => step.concept_id === id)?.title || "Untitled concept";
  const linkCount = steps.reduce((count, step) => count + (step.prerequisites || []).length, 0);
  const toReview = conceptsToReview(steps);
  const clearSelection = useCallback(() => setSelectedId(null), []);
  const closeUndo = useCallback(() => setUndo(null), []);
  const closeConfirm = useCallback(() => setConfirm(null), []);

  useEffect(() => {
    if (selectedId !== null && !selected) setSelectedId(null);
  }, [selectedId, selected]);

  function ask(next) {
    setConfirmError("");
    setConfirm(next);
  }

  // Runs a confirmed change; on success the preview is replaced and Undo offered.
  async function apply(call, message) {
    setBusy(true);
    setConfirmError("");
    try {
      const data = await call();
      if (onPathData) onPathData(data);
      setConfirm(null);
      setUndo(data.undo?.length ? { message, records: data.undo, error: "" } : null);
    } catch (error) {
      setConfirmError(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleUndo() {
    if (!undo) return;
    setBusy(true);
    try {
      const data = await restorePathLinks(topicId, undo.records);
      if (onPathData) onPathData(data);
      setUndo(null);
    } catch (error) {
      setUndo((current) => current && { ...current, error: error.message });
    } finally {
      setBusy(false);
    }
  }

  // Dropping B (dragged) onto A (target): teach A before B.
  function handleDrop(draggedId, targetId) {
    const b = titleOf(draggedId);
    const a = titleOf(targetId);
    const drop = classifyDrop(steps, draggedId, targetId);
    if (drop.kind === "self") return;
    if (drop.kind === "already") {
      ask({ title: `${a} is already taught before ${b}.`, actions: [], cancelLabel: "OK" });
      return;
    }
    const add = () => apply(() => addPathLink(topicId, targetId, draggedId), `${a} is now taught before ${b}.`);
    if (drop.kind === "add") {
      ask({ title: `Teach ${a} before ${b}?`, actions: [{ label: "Yes", primary: true, onClick: add }] });
      return;
    }
    const current = drop.current.map((entry) => entry.title).join(", ");
    ask({
      title: `${b} already comes after ${current}. What do you want?`,
      actions: [
        { label: `Add ${a} as another prerequisite`, onClick: add },
        {
          label: `Move: ${a} replaces ${current}`,
          primary: true,
          onClick: () => apply(() => movePathLink(topicId, targetId, draggedId), `${b} now comes only after ${a}.`),
        },
      ],
    });
  }

  // A recommendation sits on the concept it would change: "link" may need to
  // come before "step".
  function handleAccept(link, step) {
    ask({
      title: `Teach ${link.title} before ${step.title}?`,
      actions: [{
        label: "Yes",
        primary: true,
        onClick: () => apply(
          () => decidePathLink(topicId, link.link_id, "approved"),
          `${link.title} is now taught before ${step.title}.`,
        ),
      }],
    });
  }

  function handleReject(link, step) {
    ask({
      title: `Don't teach ${link.title} before ${step.title}?`,
      message: "It won't be suggested again.",
      actions: [{
        label: "Yes",
        primary: true,
        onClick: () => apply(
          () => decidePathLink(topicId, link.link_id, "rejected"),
          `${link.title} → ${step.title} won't be suggested again.`,
        ),
      }],
    });
  }

  function handleRemove(link, step) {
    ask({
      title: `${link.title} no longer has to come before ${step.title}?`,
      message: "It won't be suggested again.",
      actions: [{
        label: "Yes",
        primary: true,
        onClick: () => apply(
          () => decidePathLink(topicId, link.link_id, "rejected"),
          `${link.title} no longer has to come before ${step.title}.`,
        ),
      }],
    });
  }

  return (
    <section className="path-material">
      <header className="path-material-head">
        <h3>{path.topic_title || "Learning path"}</h3>
      </header>

      {linkCount === 0 && steps.length > 0 && (
        <p className="muted-text path-ordering-note">
          Ordered as your lesson files present it. Nothing has to be learned before anything else yet
          {canEdit ? " — drag a concept onto the one that must come before it" : ""}.
        </p>
      )}

      {editable && path.diagnostics?.changed_since_publish && (
        <p className="path-changed-note" role="status">
          You changed what must be learned first after the last publish. Students still follow
          the published path until you publish again.
        </p>
      )}

      {canEdit && toReview > 0 && (
        <p className="pg-review-note" role="status">
          <span className="pg-node-pending" aria-hidden="true">!</span>
          <strong>
            {toReview} concept{toReview === 1 ? " has" : "s have"} recommended links to review.
          </strong>{" "}
          Click a concept marked in yellow to see them.
        </p>
      )}

      <div className="pg-graph-area">
        <PathGraph
          steps={steps}
          selectedId={selectedId}
          onSelect={setSelectedId}
          editable={canEdit && !busy}
          showPending={canEdit}
          onDrop={handleDrop}
        />
        <ConceptDetails
          step={selected}
          editable={canEdit}
          busy={busy}
          onRemove={handleRemove}
          onAccept={handleAccept}
          onReject={handleReject}
          onClose={clearSelection}
        />
      </div>

      {confirm && (
        <ConfirmDialog
          title={confirm.title}
          message={confirm.message}
          actions={confirm.actions}
          cancelLabel={confirm.cancelLabel}
          error={confirmError}
          busy={busy}
          onCancel={closeConfirm}
        />
      )}

      {undo && (
        <UndoBar message={undo.message} error={undo.error} busy={busy} onUndo={handleUndo} onClose={closeUndo} />
      )}
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
        <MaterialPath key={path.topic_id ?? path.material_id} path={path} />
      ))}
    </div>
  );
}
