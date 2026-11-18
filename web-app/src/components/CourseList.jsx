import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { deleteCourse, fetchCourses } from "../api";

export default function CourseList({ refreshKey }) {
  const [courses, setCourses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deletingId, setDeletingId] = useState(null);
  const [courseToDelete, setCourseToDelete] = useState(null);

  async function loadCourses() {
    const data = await fetchCourses();
    setCourses(data);
    setError("");
  }

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await fetchCourses();
        if (!cancelled) {
          setCourses(data);
          setError("");
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  async function confirmDeleteCourse() {
    if (!courseToDelete) return;

    setDeletingId(courseToDelete.id);
    setError("");
    try {
      await deleteCourse(courseToDelete.id);
      await loadCourses();
      setCourseToDelete(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setDeletingId(null);
    }
  }

  if (loading) return <div className="empty-state">Loading courses...</div>;
  if (error) return <div className="error-banner">{error}</div>;
  if (!courses.length) {
    return <div className="empty-state">No courses yet. Create one, then upload an outline.</div>;
  }

  return (
    <>
      <div className="lesson-list">
        {courses.map((course) => (
          <div key={course.id} className="lesson-item course-list-item">
            <Link to={`/courses/${course.id}`} className="course-list-link">
              <div className="lesson-meta">
                <strong>{course.title}</strong>
                <span>{course.node_count} extracted topics</span>
                <span>
                  {course.outline_approved
                    ? "Confirmed hierarchy"
                    : course.has_outline
                    ? "Outline pending confirmation"
                    : "No outline yet"}
                </span>
              </div>
              <span className={`status-pill ${course.outline_approved ? "status-completed" : "status-pending"}`}>
                {course.outline_approved ? "Hierarchy ready" : course.has_outline ? "Needs review" : "Needs outline"}
              </span>
            </Link>
            <button
              type="button"
              className="btn btn-danger btn-small"
              disabled={deletingId === course.id}
              onClick={() => setCourseToDelete(course)}
            >
              {deletingId === course.id ? "Deleting..." : "Delete"}
            </button>
          </div>
        ))}
      </div>

      {courseToDelete && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal-card" role="dialog" aria-modal="true" aria-labelledby="delete-course-title">
            <h3 id="delete-course-title">Delete course?</h3>
            <p>
              This will remove "{courseToDelete.title}" and its outline, materials, and generated lesson
              content.
            </p>
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setCourseToDelete(null)}
                disabled={deletingId === courseToDelete.id}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={confirmDeleteCourse}
                disabled={deletingId === courseToDelete.id}
              >
                {deletingId === courseToDelete.id ? "Deleting..." : "Delete course"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
