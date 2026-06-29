import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import CourseDAG from "../components/CourseDAG";
import {
  fetchCourse,
  fetchPublishedLessons,
  uploadCourseOutline,
  uploadLessonToNode,
} from "../api";

export default function CourseDetailPage() {
  const { id } = useParams();
  const [course, setCourse] = useState(null);
  const [published, setPublished] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState("professor");

  async function loadCourse() {
    const data = await fetchCourse(id);
    setCourse(data);
    const pub = await fetchPublishedLessons(id);
    setPublished(pub);
  }

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        if (!cancelled) {
          await loadCourse();
          setError("");
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    }

    load();
    const interval = setInterval(load, 4000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [id]);

  async function handleOutlineUpload(event) {
    const file = event.target.files?.[0];
    if (!file) return;

    setBusy(true);
    setError("");
    try {
      const formData = new FormData();
      formData.append("outline_file", file);
      const data = await uploadCourseOutline(id, formData);
      setCourse(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
      event.target.value = "";
    }
  }

  async function handleLessonUpload(nodeId, file) {
    setBusy(true);
    setError("");
    try {
      const formData = new FormData();
      formData.append("pdf_file", file);
      await uploadLessonToNode(id, nodeId, formData);
      await loadCourse();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

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
          ← Back to courses
        </Link>
        <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem", flexWrap: "wrap", marginTop: "0.75rem" }}>
          <div>
            <h2>{course.title}</h2>
            {course.description && (
              <p style={{ color: "var(--muted)", marginTop: "0.5rem" }}>{course.description}</p>
            )}
          </div>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button
              type="button"
              className={`btn ${view === "professor" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setView("professor")}
            >
              Professor view
            </button>
            <button
              type="button"
              className={`btn ${view === "student" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setView("student")}
            >
              Student view
            </button>
          </div>
        </div>
        {error && <div className="error-banner">{error}</div>}
      </section>

      {view === "professor" ? (
        <>
          <section className="card" style={{ marginBottom: "1.25rem" }}>
            <h3>Step 2 — Upload course outline</h3>
            <p style={{ color: "var(--muted)", marginBottom: "1rem" }}>
              Upload a .txt, .md, or .pdf outline. Mavia builds a DAG of empty lesson nodes from
              your table of contents. Later, each lesson PDF you upload will fill the matching node.
            </p>
            <label className="btn btn-primary">
              {busy ? "Uploading..." : "Upload outline file"}
              <input
                type="file"
                accept=".txt,.md,.pdf,text/plain,text/markdown,application/pdf"
                hidden
                disabled={busy}
                onChange={handleOutlineUpload}
              />
            </label>
            {course.outline && (
              <p style={{ color: "var(--muted)", marginTop: "0.75rem" }}>
                Current outline: {course.outline.filename}
              </p>
            )}
          </section>

          <section className="card">
            <h3>Step 3 — Lesson hierarchy (DAG)</h3>
            <CourseDAG dag={course.dag} courseId={course.id} onUpload={handleLessonUpload} />
          </section>
        </>
      ) : (
        <section className="card">
          <h3>Published audio lessons</h3>
          {!published.length ? (
            <div className="empty-state">
              No published lessons yet. Professors must approve scripts, generate audio, and publish
              before students can listen.
            </div>
          ) : (
            <div className="lesson-list">
              {published.map((lesson) => (
                <Link key={lesson.id} to={`/lessons/${lesson.id}?student=1`} className="lesson-item">
                  <div className="lesson-meta">
                    <strong>{lesson.title}</strong>
                    <span>{lesson.module_count} audio chapters</span>
                  </div>
                  <span className="status-pill status-completed">Published</span>
                </Link>
              ))}
            </div>
          )}
        </section>
      )}
    </>
  );
}
