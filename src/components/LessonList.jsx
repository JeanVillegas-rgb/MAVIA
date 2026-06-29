import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchLessons } from "../api";

function formatDate(value) {
  return new Date(value).toLocaleString();
}

export default function LessonList({ refreshKey }) {
  const [lessons, setLessons] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError("");
      try {
        const data = await fetchLessons();
        if (!cancelled) {
          setLessons(data);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    load();
    const interval = setInterval(load, 4000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [refreshKey]);

  if (loading && lessons.length === 0) {
    return <div className="empty-state">Loading lessons...</div>;
  }

  if (error) {
    return <div className="error-banner">{error}</div>;
  }

  if (lessons.length === 0) {
    return (
      <div className="empty-state">
        No science lessons yet. Upload a PDF to create your first audiobook module.
      </div>
    );
  }

  return (
    <div className="lesson-list">
      {lessons.map((lesson) => (
        <Link key={lesson.id} to={`/lessons/${lesson.id}`} className="lesson-item">
          <div className="lesson-meta">
            <strong>{lesson.title}</strong>
            <span>{formatDate(lesson.created_at)}</span>
            <span>{lesson.module_count} audio modules</span>
          </div>
          <div>
            <span className={`status-pill status-${lesson.status}`}>{lesson.status}</span>
            {lesson.status === "processing" && (
              <div className="progress-bar" style={{ width: 140, marginTop: 8 }}>
                <span style={{ width: `${lesson.progress}%` }} />
              </div>
            )}
          </div>
        </Link>
      ))}
    </div>
  );
}
