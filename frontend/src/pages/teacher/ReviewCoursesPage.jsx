import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import TeacherShell from "./TeacherShell";
import { fetchCourses } from "../../api";

// Read-only teacher review of existing narration and detected questions.
export default function ReviewCoursesPage() {
  const [courses, setCourses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetchCourses()
      .then((data) => {
        if (!cancelled) setCourses(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <TeacherShell>
      <div className="mv-page-head">
        <h1>Review courses</h1>
        <p>
          Inspect lesson narration and detected questions, organized by course
          and module.
        </p>
      </div>

      {loading && <div className="empty-state">Loading courses…</div>}
      {error && <div className="error-banner">{error}</div>}
      {!loading && !error && !courses.length && (
        <div className="empty-state">
          No courses yet. Create one from the Courses tab, then upload its outline.
        </div>
      )}

      {!loading && !error && courses.length > 0 && (
        <div className="review-card-grid">
          {courses.map((course) => (
            <Link
              key={course.id}
              to={`/review/courses/${course.id}`}
              className="mv-card review-course-card"
            >
              <div className="review-course-card__art" aria-hidden="true">
                ♪
              </div>
              <strong className="review-course-card__title">{course.title}</strong>
              {course.description && (
                <p className="review-course-card__desc">{course.description}</p>
              )}
              <span className="review-course-card__meta">
                {course.outline_approved
                  ? `${course.node_count} topic${course.node_count === 1 ? "" : "s"}`
                  : course.has_outline
                    ? "Outline pending confirmation"
                    : "No outline yet"}
              </span>
            </Link>
          ))}
        </div>
      )}
    </TeacherShell>
  );
}
