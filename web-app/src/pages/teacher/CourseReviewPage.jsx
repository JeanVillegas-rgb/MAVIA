import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import TeacherShell from "./TeacherShell";
import {
  addEnrollment,
  fetchCourse,
  fetchCourseProgress,
  fetchReviewModules,
  removeEnrollment,
  searchStudents,
} from "../../api";

function pct(value) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function timeAgo(iso) {
  if (!iso) return "Not started";
  const then = new Date(iso).getTime();
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function AddStudent({ courseId, onAdded, existingIds }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    const handle = setTimeout(() => {
      searchStudents(query)
        .then((rows) => {
          if (!cancelled) setResults(rows);
        })
        .catch((err) => {
          if (!cancelled) setError(err.message);
        });
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [open, query]);

  async function enroll(student) {
    setBusyId(student.id);
    setError("");
    try {
      await addEnrollment(courseId, student.id);
      onAdded();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  }

  if (!open) {
    return (
      <button type="button" className="mv-btn mv-btn--soft" onClick={() => setOpen(true)}>
        ＋ Add student
      </button>
    );
  }

  return (
    <div className="review-add-student">
      <div className="review-add-student__row">
        <input
          type="search"
          autoFocus
          placeholder="Search students by name or email"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button type="button" className="mv-btn mv-btn--ghost" onClick={() => setOpen(false)}>
          Done
        </button>
      </div>
      {error && <div className="error-banner">{error}</div>}
      <ul className="review-add-student__results">
        {results.length === 0 && <li className="mv-muted">No matching students.</li>}
        {results.map((student) => {
          const already = existingIds.includes(student.id);
          return (
            <li key={student.id}>
              <span>
                <strong>{student.name}</strong>{" "}
                <span className="mv-muted">{student.email}</span>
              </span>
              <button
                type="button"
                className="mv-btn mv-btn--soft"
                disabled={already || busyId === student.id}
                onClick={() => enroll(student)}
              >
                {already ? "Enrolled" : busyId === student.id ? "Adding…" : "Add"}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default function CourseReviewPage() {
  const { courseId } = useParams();
  const [course, setCourse] = useState(null);
  const [modules, setModules] = useState([]);
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const loadProgress = useCallback(() => {
    return fetchCourseProgress(courseId)
      .then(setProgress)
      .catch((err) => setError(err.message));
  }, [courseId]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetchCourse(courseId),
      fetchReviewModules(courseId),
      fetchCourseProgress(courseId),
    ])
      .then(([courseData, moduleData, progressData]) => {
        if (cancelled) return;
        setCourse(courseData);
        setModules(moduleData);
        setProgress(progressData);
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
  }, [courseId]);

  async function unenroll(enrollmentId) {
    try {
      await removeEnrollment(courseId, enrollmentId);
      await loadProgress();
    } catch (err) {
      setError(err.message);
    }
  }

  const rows = progress?.rows ?? [];
  const existingIds = rows.map((row) => row.student.id);

  return (
    <TeacherShell>
      <div className="mv-page-head">
        <h1>{course ? course.title : "Course"}</h1>
        <p>
          <Link to="/review">← All courses</Link>
        </p>
      </div>

      {loading && <div className="empty-state">Loading…</div>}
      {error && <div className="error-banner">{error}</div>}

      {!loading && (
        <>
          <section className="mv-card">
            <h3 className="mv-card__title">Modules</h3>
            {modules.length === 0 ? (
              <div className="empty-state">
                No modules with generated content yet. Publish topics and generate
                their lesson audio first.
              </div>
            ) : (
              <div className="mv-list">
                {modules.map((module) => (
                  <div key={module.id} className="mv-list__item">
                    <span>
                      <strong>{module.title}</strong>
                      <span className="mv-muted">
                        {" "}
                        · {module.lesson_count} lesson
                        {module.lesson_count === 1 ? "" : "s"} · {module.track_count} track
                        {module.track_count === 1 ? "" : "s"}
                      </span>
                      {module.question_count === 0 && (
                        <span className="review-tag"> Review only — no questions</span>
                      )}
                    </span>
                    <Link
                      to={`/review/courses/${courseId}/modules/${module.id}`}
                      className="mv-btn mv-btn--soft"
                    >
                      ▶ Play module
                    </Link>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="mv-card">
            <div className="review-progress-head">
              <h3 className="mv-card__title" style={{ margin: 0 }}>
                Student progress
              </h3>
              <AddStudent
                courseId={courseId}
                existingIds={existingIds}
                onAdded={loadProgress}
              />
            </div>

            {progress && !progress.content_ready && (
              <div className="empty-state" style={{ marginBottom: "1rem" }}>
                This course has no question content yet, so mastery can't be tracked.
                Enrolled students still appear below.
              </div>
            )}

            {rows.length === 0 ? (
              <div className="empty-state">
                No students enrolled. Use “Add student” to build the roster.
              </div>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table className="mv-table">
                  <thead>
                    <tr>
                      <th>Student</th>
                      <th>Mastery</th>
                      <th>Answered</th>
                      <th>Correct</th>
                      <th>Modules</th>
                      <th>Last activity</th>
                      <th aria-label="Remove" />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.student.id}>
                        <td>
                          <strong>{row.student.name}</strong>
                          <div className="mv-muted" style={{ fontSize: ".8rem" }}>
                            {row.student.email}
                          </div>
                        </td>
                        <td>
                          {row.started ? (
                            <span className="review-mastery">
                              <span
                                className="mv-progress"
                                style={{ width: 90 }}
                                aria-label={`${pct(row.mastery)} mastery`}
                              >
                                <span
                                  className="mv-progress__fill"
                                  style={{ width: pct(row.mastery) }}
                                />
                              </span>
                              {pct(row.mastery)}
                            </span>
                          ) : (
                            <span className="mv-muted">Not started</span>
                          )}
                        </td>
                        <td>{row.questions_answered}</td>
                        <td>{row.correct_rate == null ? "—" : pct(row.correct_rate)}</td>
                        <td>
                          {row.modules_completed} / {row.total_modules}
                          {row.completed && " ✓"}
                        </td>
                        <td className="mv-muted">{timeAgo(row.last_activity)}</td>
                        <td>
                          <button
                            type="button"
                            className="mv-btn mv-btn--ghost"
                            title="Remove from course"
                            onClick={() => unenroll(row.enrollment_id)}
                          >
                            ×
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </TeacherShell>
  );
}
