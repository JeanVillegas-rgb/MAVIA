import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import CourseHierarchy from "../components/CourseHierarchy";
import {
  confirmCourseOutline,
  createOutlineNode,
  deleteCourseOutline,
  deleteOutlineNode,
  fetchCourse,
  updateOutlineNode,
  uploadCourseOutline,
  uploadLearningMaterial,
} from "../api";

function formatMatchedMaterialLocation(material) {
  const path = material?.outline_node_path || [];
  if (path.length > 1) {
    return `Lesson PDF uploaded and classified to \nTopic: ${path[0].title}. \nSubtopic: ${path
      .slice(1)
      .map((node) => node.title)
      .join(" > ")}.`;
  }

  if (path.length === 1) {
    return `Lesson PDF uploaded and classified to \nTopic: ${path[0].title}.`;
  }

  if (material?.module_node_title) {
    return `Lesson PDF uploaded. Topic: ${material.module_node_title}. \nNo matching subtopic was found.`;
  }

  return "Lesson PDF uploaded, but its topic placement could not be determined.";
}

export default function CourseDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [course, setCourse] = useState(null);
  const [error, setError] = useState("");
  const [outlineBusy, setOutlineBusy] = useState(false);
  const [hierarchyBusy, setHierarchyBusy] = useState(false);
  const [materialBusy, setMaterialBusy] = useState(false);
  const [uploadMessage, setUploadMessage] = useState("");
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [nodeToDelete, setNodeToDelete] = useState(null);
  const outlineInputRef = useRef(null);

  async function loadCourse() {
    const data = await fetchCourse(id);
    setCourse(data);
  }

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await fetchCourse(id);
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
  }, [id]);

  async function handleOutlineUpload(event) {
    const file = event.target.files?.[0];
    if (!file) return;

    setOutlineBusy(true);
    setError("");
    try {
      const formData = new FormData();
      formData.append("outline_file", file);
      await uploadCourseOutline(id, formData);
      await loadCourse();
    } catch (err) {
      setError(err.message);
    } finally {
      setOutlineBusy(false);
      event.target.value = "";
    }
  }

  async function handleDeleteOutline() {
    if (!course?.outline) return;
    setShowDeleteConfirm(true);
  }

  async function confirmDeleteOutline() {
    setOutlineBusy(true);
    setError("");
    try {
      const data = await deleteCourseOutline(id);
      setCourse(data);
      setShowDeleteConfirm(false);
      if (outlineInputRef.current) {
        outlineInputRef.current.value = "";
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setOutlineBusy(false);
    }
  }

  async function handleConfirmOutline() {
    if (!course?.outline || !course?.hierarchy?.length) return;

    setOutlineBusy(true);
    setError("");
    try {
      const data = await confirmCourseOutline(id);
      setCourse(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setOutlineBusy(false);
    }
  }

  async function handleAutoMaterialUpload(event) {
    const file = event.target.files?.[0];
    if (!file) return;

    setMaterialBusy(true);
    setError("");
    setUploadMessage("");
    try {
      const existingMaterialIds = new Set((course?.materials || []).map((material) => material.id));
      const formData = new FormData();
      formData.append("pdf_file", file);
      formData.append("title", file.name.replace(/\.pdf$/i, ""));
      const updatedCourse = await uploadLearningMaterial(id, formData);
      setCourse(updatedCourse);

      const uploadedMaterial = (updatedCourse.materials || []).find(
        (material) => !existingMaterialIds.has(material.id)
      );
      setUploadMessage(formatMatchedMaterialLocation(uploadedMaterial));
    } catch (err) {
      setError(err.message);
    } finally {
      setMaterialBusy(false);
      event.target.value = "";
    }
  }

  async function handleCreateNode(data) {
    setHierarchyBusy(true);
    setError("");
    try {
      const updatedCourse = await createOutlineNode(id, data);
      setCourse(updatedCourse);
    } catch (err) {
      setError(err.message);
    } finally {
      setHierarchyBusy(false);
    }
  }

  async function handleUpdateNode(nodeId, data) {
    setHierarchyBusy(true);
    setError("");
    try {
      const updatedCourse = await updateOutlineNode(id, nodeId, data);
      setCourse(updatedCourse);
    } catch (err) {
      setError(err.message);
    } finally {
      setHierarchyBusy(false);
    }
  }

  function handleDeleteNode(node) {
    setNodeToDelete(node);
  }

  async function confirmDeleteNode() {
    if (!nodeToDelete) return;
    setHierarchyBusy(true);
    setError("");
    try {
      const updatedCourse = await deleteOutlineNode(id, nodeToDelete.id);
      setCourse(updatedCourse);
      setNodeToDelete(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setHierarchyBusy(false);
    }
  }

  const hierarchyConfirmed = Boolean(course?.outline?.is_approved);

  if (error && !course) {
    return (
      <div className="card">
        <div className="error-banner">{error}</div>
        <Link to="/" className="btn btn-secondary" style={{ display: "inline-block", marginTop: "1rem" }}>
          Back home
        </Link>
      </div>
    );
  }

  if (!course) return <div className="empty-state">Loading course...</div>;

  return (
    <>
      <section className="card" style={{ marginBottom: "1.25rem" }}>
        <Link to="/" style={{ color: "var(--muted)" }}>
          Back to courses
        </Link>
        <div className="course-header-row">
          <div>
            <h2>{course.title}</h2>
            {course.description && (
              <p style={{ color: "var(--muted)", marginTop: "0.5rem" }}>{course.description}</p>
            )}
          </div>
        </div>
        {error && <div className="error-banner">{error}</div>}
      </section>

      {!course.outline?.is_approved && (
        <section className="card" style={{ marginBottom: "1.25rem" }}>
          <h3>Course outline extraction</h3>
          <p className="muted-text">
            Upload a PDF course outline. Mavia extracts a draft hierarchy. Review it, then
            confirm it before lesson PDFs are mapped to the approved topics.
          </p>
          <div className="action-row">
            <label className="btn btn-primary">
              {outlineBusy ? "Extracting..." : "Upload outline"}
              <input
                ref={outlineInputRef}
                type="file"
                accept=".pdf,application/pdf"
                hidden
                disabled={outlineBusy}
                onChange={handleOutlineUpload}
              />
            </label>
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={handleDeleteOutline}
              disabled={outlineBusy || !course.outline}
            >
              Delete outline
            </button>
          </div>
          {course.outline && (
            <p className="muted-text">
              Current outline: {course.outline.filename} (pending teacher confirmation)
            </p>
          )}
        </section>
      )}

      {course.outline?.is_approved && (
        <section className="card" style={{ marginBottom: "1.25rem" }}>
          <h3>Upload lesson PDF</h3>
          <p className="muted-text">
            Upload a lesson PDF and TF-IDF cosine similarity will match it to the most suitable topic or subtopic.
          </p>
          <div className="action-row">
            <label className="btn btn-primary">
              {materialBusy ? "Uploading..." : "Upload lesson PDF"}
              <input
                type="file"
                accept=".pdf,application/pdf"
                hidden
                disabled={materialBusy}
                onChange={handleAutoMaterialUpload}
              />
            </label>
          </div>
          {uploadMessage && <div className="success-banner lesson-placement-banner">{uploadMessage}</div>}
        </section>
      )}

      <section className="card" style={{ marginBottom: "1.25rem" }}>
        <div className="section-heading-row">
          <div>
            <h3>{hierarchyConfirmed ? "Lesson Hierarchy" : "Edit Lesson Hierarchy"}</h3>
            <p className="muted-text">
              {hierarchyConfirmed
                ? "Click a topic to open its learning material screen."
                : "These are the topic nodes. Confirm the hierarchy before uploading learning materials."}
              </p>
          </div>
        </div>
        {course.outline && !course.outline.is_approved && course.hierarchy?.length > 0 && (
          <p className="muted-text">
            Review and organize the extracted topics. Confirm only if the hierarchy is correct.
          </p>
        )}
        {course.outline?.is_approved && (
          <p className="muted-text">Editing is hidden after confirmation so generated content stays mapped to the approved topics.</p>
        )}
        <CourseHierarchy
          hierarchy={course.hierarchy}
          busy={hierarchyBusy || outlineBusy}
          readOnly={hierarchyConfirmed}
          onSelectNode={(node) => navigate(`/courses/${id}/topics/${node.id}`)}
          onCreateNode={handleCreateNode}
          onUpdateNode={handleUpdateNode}
          onDeleteNode={handleDeleteNode}
        />
        {course.outline && !course.outline.is_approved && course.hierarchy?.length > 0 && (
          <div className="bottom-action-row">
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleConfirmOutline}
              disabled={outlineBusy}
            >
              {outlineBusy ? "Confirming..." : "Confirm hierarchy"}
            </button>
          </div>
        )}
      </section>

      {showDeleteConfirm && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal-card" role="dialog" aria-modal="true" aria-labelledby="delete-outline-title">
            <h3 id="delete-outline-title">Delete outline?</h3>
            <p>
              This will remove the uploaded outline and the extracted draft hierarchy for this
              course.
            </p>
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setShowDeleteConfirm(false)}
                disabled={outlineBusy}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={confirmDeleteOutline}
                disabled={outlineBusy}
              >
                {outlineBusy ? "Deleting..." : "Delete outline"}
              </button>
            </div>
          </div>
        </div>
      )}

      {nodeToDelete && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal-card" role="dialog" aria-modal="true" aria-labelledby="delete-node-title">
            <h3 id="delete-node-title">Delete topic?</h3>
            <p>
              This will remove "{nodeToDelete.title}" from the hierarchy. Any subtopics under it
              will also be removed.
            </p>
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setNodeToDelete(null)}
                disabled={hierarchyBusy}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={confirmDeleteNode}
                disabled={hierarchyBusy}
              >
                {hierarchyBusy ? "Deleting..." : "Delete topic"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
