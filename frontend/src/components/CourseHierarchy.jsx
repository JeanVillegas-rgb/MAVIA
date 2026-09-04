import { useState } from "react";

const GROUP_COLORS = ["blue", "green", "violet", "amber"];

function countDescendants(node) {
  return (node.children || []).reduce((total, child) => total + 1 + countDescendants(child), 0);
}

function flattenRelatedInfo(value) {
  if (!value || typeof value !== "object") return [];
  return Object.entries(value).flatMap(([key, item]) => {
    const label = key.replace(/_/g, " ");
    if (Array.isArray(item)) {
      return item.filter(Boolean).map((entry) => ({ label, value: String(entry) }));
    }
    if (item && typeof item === "object") {
      return flattenRelatedInfo(item);
    }
    return item ? [{ label, value: String(item) }] : [];
  });
}

function RelatedInfo({ info }) {
  const items = flattenRelatedInfo(info).slice(0, 4);
  if (!items.length) return null;

  return (
    <div className="hierarchy-related-info">
      {items.map((item, index) => (
        <p key={`${item.label}-${index}`}>
          <span>{item.label}:</span> {item.value}
        </p>
      ))}
    </div>
  );
}

function LessonPdfBadge({ count = 0 }) {
  if (!count) return null;

  return (
    <span
      className="hierarchy-pdf-badge"
      title={`${count} lesson PDF${count === 1 ? "" : "s"} uploaded here`}
      aria-label={`${count} lesson PDF${count === 1 ? "" : "s"} uploaded here`}
    >
      <span aria-hidden="true">PDF</span>
      {count}
    </span>
  );
}

function IconButton({ label, children, tone = "quiet", ...props }) {
  return (
    <button
      type="button"
      className={`hierarchy-icon-button hierarchy-icon-button-${tone}`}
      aria-label={label}
      title={label}
      {...props}
    >
      {children}
    </button>
  );
}

function InlineTitleForm({ initialTitle, label, busy, onSave, onCancel }) {
  const [title, setTitle] = useState(initialTitle);

  async function submit(event) {
    event.preventDefault();
    const nextTitle = title.trim();
    if (!nextTitle) return;
    await onSave(nextTitle);
  }

  return (
    <form className="hierarchy-inline-form" onSubmit={submit}>
      <input
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        disabled={busy}
        aria-label={label}
        autoFocus
      />
      <button type="submit" className="btn btn-primary btn-small" disabled={busy || !title.trim()}>
        Save
      </button>
      <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={onCancel}>
        Cancel
      </button>
    </form>
  );
}

function AddChildForm({ parentTitle, busy, onSubmit, onCancel }) {
  const [title, setTitle] = useState("");

  async function submit(event) {
    event.preventDefault();
    const nextTitle = title.trim();
    if (!nextTitle) return;
    await onSubmit(nextTitle);
  }

  return (
    <form className="hierarchy-child-form" onSubmit={submit}>
      <input
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        placeholder={`Add subtopic under ${parentTitle}`}
        disabled={busy}
        aria-label={`Add subtopic under ${parentTitle}`}
        autoFocus
      />
      <button type="submit" className="btn btn-primary btn-small" disabled={busy || !title.trim()}>
        Add
      </button>
      <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={onCancel}>
        Cancel
      </button>
    </form>
  );
}

function ChildTopicRow({
  node,
  color,
  busy,
  readOnly,
  onSelectNode,
  onCreateNode,
  onUpdateNode,
  onDeleteNode,
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [isAddingChild, setIsAddingChild] = useState(false);

  async function saveTitle(nextTitle) {
    if (nextTitle !== node.title) {
      await onUpdateNode(node.id, { title: nextTitle });
    }
    setIsEditing(false);
  }

  async function addChild(title) {
    await onCreateNode({ title, parent: node.id });
    setIsAddingChild(false);
  }

  return (
    <div className="hierarchy-child-wrap">
      <div className={`hierarchy-child-row ${readOnly ? "is-clickable" : ""}`}>
        <span className="hierarchy-grip" aria-hidden="true">::</span>
        <span className={`hierarchy-dot hierarchy-dot-${color}`} aria-hidden="true" />
        <div className="hierarchy-title-stack">
          {isEditing ? (
            <InlineTitleForm
              initialTitle={node.title}
              label={`Rename ${node.title}`}
              busy={busy}
              onSave={saveTitle}
              onCancel={() => setIsEditing(false)}
            />
          ) : (
            readOnly ? (
              <button
                type="button"
                className="hierarchy-title-button"
                onClick={() => onSelectNode(node)}
              >
                <span className="hierarchy-title-line">
                  <span>{node.title}</span>
                  <LessonPdfBadge count={node.lesson_pdf_count} />
                </span>
              </button>
            ) : (
              <strong className="hierarchy-title-line">
                <span>{node.title}</span>
                <LessonPdfBadge count={node.lesson_pdf_count} />
              </strong>
            )
          )}
          {!isEditing && <RelatedInfo info={node.related_info} />}
        </div>
        {!readOnly && !isEditing && (
          <div className="hierarchy-row-actions">
            <IconButton label={`Add child under ${node.title}`} disabled={busy} onClick={() => setIsAddingChild(true)}>
              +
            </IconButton>
            <IconButton label={`Edit ${node.title}`} disabled={busy} onClick={() => setIsEditing(true)}>
              edit
            </IconButton>
            <IconButton label={`Delete ${node.title}`} tone="danger" disabled={busy} onClick={() => onDeleteNode(node)}>
              x
            </IconButton>
          </div>
        )}
      </div>

      {isAddingChild && (
        <AddChildForm
          parentTitle={node.title}
          busy={busy}
          onSubmit={addChild}
          onCancel={() => setIsAddingChild(false)}
        />
      )}

      {node.children?.length > 0 && (
        <div className="hierarchy-nested-children">
          {node.children.map((child) => (
            <ChildTopicRow
              key={child.id}
              node={child}
              color={color}
              busy={busy}
              readOnly={readOnly}
              onSelectNode={onSelectNode}
              onCreateNode={onCreateNode}
              onUpdateNode={onUpdateNode}
              onDeleteNode={onDeleteNode}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function TopLevelTopic({
  node,
  index,
  busy,
  readOnly,
  onSelectNode,
  onCreateNode,
  onUpdateNode,
  onDeleteNode,
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [isAddingChild, setIsAddingChild] = useState(false);
  const color = GROUP_COLORS[index % GROUP_COLORS.length];
  const childCount = node.children?.length || 0;

  async function saveTitle(nextTitle) {
    if (nextTitle !== node.title) {
      await onUpdateNode(node.id, { title: nextTitle });
    }
    setIsEditing(false);
  }

  async function addChild(title) {
    await onCreateNode({ title, parent: node.id });
    setIsAddingChild(false);
  }

  return (
    <section className={`hierarchy-section hierarchy-section-${color}`}>
      <div className={`hierarchy-section-header ${readOnly ? "is-clickable" : ""}`}>
        <div className="hierarchy-section-main">
          <span className={`hierarchy-number hierarchy-number-${color}`}>{index + 1}</span>
          <span className="hierarchy-chevron" aria-hidden="true">v</span>
          <div className="hierarchy-title-stack">
            {isEditing ? (
              <InlineTitleForm
                initialTitle={node.title}
                label={`Rename ${node.title}`}
                busy={busy}
                onSave={saveTitle}
                onCancel={() => setIsEditing(false)}
              />
            ) : (
              readOnly ? (
                <button
                  type="button"
                  className="hierarchy-title-button hierarchy-title-button-strong"
                  onClick={() => onSelectNode(node)}
                >
                  <span className="hierarchy-title-line">
                    <span>{node.title}</span>
                    <LessonPdfBadge count={node.lesson_pdf_count} />
                  </span>
                </button>
              ) : (
                <h4 className="hierarchy-title-line">
                  <span>{node.title}</span>
                  <LessonPdfBadge count={node.lesson_pdf_count} />
                </h4>
              )
            )}
            {!isEditing && <RelatedInfo info={node.related_info} />}
          </div>
        </div>

        {!readOnly && !isEditing && (
          <div className="hierarchy-section-actions">
            <span className={`hierarchy-count hierarchy-count-${color}`}>
              {childCount} {childCount === 1 ? "topic" : "topics"}
            </span>
            <IconButton label={`Add child under ${node.title}`} disabled={busy} onClick={() => setIsAddingChild(true)}>
              +
            </IconButton>
            <IconButton label={`Edit ${node.title}`} disabled={busy} onClick={() => setIsEditing(true)}>
              edit
            </IconButton>
            <IconButton label={`Delete ${node.title}`} tone="danger" disabled={busy} onClick={() => onDeleteNode(node)}>
              x
            </IconButton>
          </div>
        )}
      </div>

      {isAddingChild && (
        <AddChildForm
          parentTitle={node.title}
          busy={busy}
          onSubmit={addChild}
          onCancel={() => setIsAddingChild(false)}
        />
      )}

      {childCount > 0 && (
        <div className="hierarchy-children">
          {node.children.map((child) => (
            <ChildTopicRow
              key={child.id}
              node={child}
              color={color}
              busy={busy}
              readOnly={readOnly}
              onSelectNode={onSelectNode}
              onCreateNode={onCreateNode}
              onUpdateNode={onUpdateNode}
              onDeleteNode={onDeleteNode}
            />
          ))}
        </div>
      )}
    </section>
  );
}

export default function CourseHierarchy({
  hierarchy,
  busy = false,
  readOnly = false,
  onSelectNode = () => {},
  onCreateNode,
  onUpdateNode,
  onDeleteNode,
}) {
  const [rootTitle, setRootTitle] = useState("");

  async function submitRoot(event) {
    event.preventDefault();
    const nextTitle = rootTitle.trim();
    if (!nextTitle) return;
    await onCreateNode({ title: nextTitle, parent: null });
    setRootTitle("");
  }

  return (
    <div className={`hierarchy-editor ${readOnly ? "is-readonly" : ""}`}>
      {!readOnly && (
        <form className="hierarchy-root-form hierarchy-root-bar" onSubmit={submitRoot}>
          <span className="hierarchy-root-plus" aria-hidden="true">o</span>
          <input
            value={rootTitle}
            onChange={(event) => setRootTitle(event.target.value)}
            placeholder="Add a new top-level topic..."
            disabled={busy}
            aria-label="Add a new top-level topic"
          />
          <button type="submit" className="btn btn-primary btn-small" disabled={busy || !rootTitle.trim()}>
            Add topic
          </button>
        </form>
      )}

      {!hierarchy?.length ? (
        <div className="empty-state">
          Upload a course outline or add a topic manually to build the lesson hierarchy.
        </div>
      ) : (
        <div className="hierarchy-sections">
          {hierarchy.map((node, index) => (
            <TopLevelTopic
              key={node.id}
              node={node}
              index={index}
              busy={busy}
              readOnly={readOnly}
              onSelectNode={onSelectNode}
              onCreateNode={onCreateNode}
              onUpdateNode={onUpdateNode}
              onDeleteNode={onDeleteNode}
            />
          ))}
        </div>
      )}

      <div className="hierarchy-tip">
        <strong>Tip:</strong>{" "}
        {readOnly
          ? "Click a topic to open its learning materials."
          : "Click a topic name to edit it, or use the small action icons at the right."}
      </div>
    </div>
  );
}
