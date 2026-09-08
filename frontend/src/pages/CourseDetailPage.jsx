import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import CourseHierarchy from "../components/CourseHierarchy";
import {
  confirmCourseOutline,
  createOutlineNode,
  deleteOutlineNode,
  fetchCourse,
  updateOutlineNode,
  uploadCoursePdf,
} from "../api";

function formatMatchedMaterialLocation(material) {
  const path = material?.outline_node_path || [];
  if (path.length > 1) {
    return `Lesson PDF uploaded and classified to\nTopic: ${path[0].title}.\nSubtopic: ${path
      .slice(1)
      .map((node) => node.title)
      .join(" > ")}.`;
  }

  if (path.length === 1) {
    return `Lesson PDF uploaded and classified to\nTopic: ${path[0].title}.`;
  }

  if (material?.module_node_title) {
    return `Lesson PDF uploaded. Topic: ${material.module_node_title}.\nNo matching subtopic was found.`;
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
  const [uploadMessage, setUploadMessage] = useState("");
  const [outlineMessage, setOutlineMessage] = useState("");
  const [nodeToDelete, setNodeToDelete] = useState(null);

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

  async function handlePdfUpload(event) {
    const file = event.target.files?.[0];
    if (!file) return;

    setOutlineBusy(true);
    setError("");
    setOutlineMessage("");
    setUploadMessage("");
    try {
      const existingMaterialIds = new Set((course?.materials || []).map((material) => material.id));
      const formData = new FormData();
      formData.append("pdf_file", file);
      formData.append("title", file.name.replace(/\.pdf$/i, ""));
      const updatedCourse = await uploadCoursePdf(id, formData);
      setCourse(updatedCourse);

      if (updatedCourse.upload_type === "outline") {
        setOutlineMessage(
          course?.outline
            ? "Course outline detected and merged automatically. Existing hierarchy nodes were preserved and new topics were added for review."
            : "Course outline detected and extracted. Review the hierarchy before confirming it."
        );
      } else {
        const uploadedMaterial = (updatedCourse.materials || []).find((material) =>
          updatedCourse.uploaded_material_id
            ? material.id === updatedCourse.uploaded_material_id
            : !existingMaterialIds.has(material.id)
        );
        if (uploadedMaterial?.status === "failed") {
          setError(
            uploadedMaterial.error_message
              || "The lesson material could not be matched to the course hierarchy."
          );
          return;
        }
        const placementMessage = formatMatchedMaterialLocation(uploadedMaterial);
        setUploadMessage(
          updatedCourse.upload_reused
            ? `This lesson PDF was already uploaded. Loaded the existing material.\n${placementMessage}`
            : `Lesson material detected automatically.\n${placementMessage}`
        );
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setOutlineBusy(false);
      event.target.value = "";
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
        <Link to="/courses" className="btn btn-secondary" style={{ display: "inline-block", marginTop: "1rem" }}>
          Back home
        </Link>
      </div>
    );
  }

  if (!course) return <div className="empty-state">Loading course...</div>;

  return (
    <>
      <section className="card" style={{ marginBottom: "1.25rem" }}>
        <Link to="/courses" style={{ color: "var(--muted)" }}>
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

      <section className="card" style={{ marginBottom: "1.25rem" }}>
        <h3>Upload course PDF</h3>
        <p className="muted-text">
          Upload either a course outline or lesson material. Mavia identifies the document automatically:
          outlines extend the hierarchy, while lesson materials are placed under the matching topic or subtopic.
        </p>
        <div className="action-row">
          <label className="btn btn-primary">
            {outlineBusy ? "Processing..." : "Upload PDF"}
            <input
              type="file"
              accept=".pdf,application/pdf"
              hidden
              disabled={outlineBusy}
              onChange={handlePdfUpload}
            />
          </label>
        </div>
        {outlineMessage && <div className="success-banner">{outlineMessage}</div>}
        {uploadMessage && <div className="success-banner lesson-placement-banner">{uploadMessage}</div>}
        {course.outline && (
          <div className="outline-source-summary">
            <strong>{course.outline.source_count || 1} outline PDF{(course.outline.source_count || 1) === 1 ? "" : "s"}</strong>
            <ul>
              {(course.outline.files || [{ id: course.outline.id, filename: course.outline.filename }]).map((file) => (
                <li key={file.id}>{file.filename}</li>
              ))}
            </ul>
            {!course.outline.is_approved && (
              <small>New outline content is pending teacher confirmation.</small>
            )}
          </div>
        )}
      </section>

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
