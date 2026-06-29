import { Link } from "react-router-dom";

const STATUS_LABELS = {
  empty: "Awaiting PDF",
  in_progress: "Processing",
  script_review: "Script review",
  audio_review: "Audio review",
  published: "Published",
};

function NodeRow({ node, courseId, onUpload }) {
  const hasLesson = Boolean(node.lesson_id);
  const isEmpty = node.status === "empty";

  return (
    <div className="dag-node" style={{ marginLeft: `${node.depth * 1.25}rem` }}>
      <div className="dag-node-header">
        <div>
          <strong>{node.title}</strong>
          <span className={`status-pill status-${node.status}`} style={{ marginLeft: "0.5rem" }}>
            {STATUS_LABELS[node.status] || node.status}
          </span>
        </div>
        <div className="dag-node-actions">
          {isEmpty && (
            <label className="btn btn-secondary btn-small">
              Upload PDF
              <input
                type="file"
                accept="application/pdf,.pdf"
                hidden
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) onUpload(node.id, file);
                  event.target.value = "";
                }}
              />
            </label>
          )}
          {hasLesson && (
            <Link to={`/lessons/${node.lesson_id}`} className="btn btn-secondary btn-small">
              Open lesson
            </Link>
          )}
        </div>
      </div>
      {node.children?.map((child) => (
        <NodeRow key={child.id} node={child} courseId={courseId} onUpload={onUpload} />
      ))}
    </div>
  );
}

export default function CourseDAG({ dag, courseId, onUpload }) {
  if (!dag?.length) {
    return (
      <div className="empty-state">
        Upload a course outline to generate the lesson hierarchy (DAG). Each node starts empty
        until you attach a matching lesson PDF.
      </div>
    );
  }

  return (
    <div className="dag-tree">
      {dag.map((node) => (
        <NodeRow key={node.id} node={node} courseId={courseId} onUpload={onUpload} />
      ))}
    </div>
  );
}
