import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchCourses } from "../api";

export default function CourseList({ refreshKey }) {
  const [courses, setCourses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

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

  if (loading) return <div className="empty-state">Loading courses...</div>;
  if (error) return <div className="error-banner">{error}</div>;
  if (!courses.length) {
    return (
      <div className="empty-state">
        No course groups yet. Create one, upload your outline, then attach lesson PDFs to each
        node in the hierarchy.
      </div>
    );
  }

  return (
    <div className="lesson-list">
      {courses.map((course) => (
        <Link key={course.id} to={`/courses/${course.id}`} className="lesson-item">
          <div className="lesson-meta">
            <strong>{course.title}</strong>
            <span>
              {course.node_count} outline nodes · {course.published_count} published
            </span>
            <span>{course.has_outline ? "Outline uploaded" : "No outline yet"}</span>
          </div>
          <span className={`status-pill ${course.has_outline ? "status-completed" : "status-pending"}`}>
            {course.has_outline ? "Ready for PDFs" : "Needs outline"}
          </span>
        </Link>
      ))}
    </div>
  );
}
