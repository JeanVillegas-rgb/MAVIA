import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import TeacherShell from "./TeacherShell";
import {
  addEnrollment,
  fetchCourse,
  fetchCourseProgress,
  fetchModulePackage,
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

// A module row that expands to show its packaged lesson content, read-only:
// the narration tracks and the questions. No playback — that's the mobile app.
function ModuleRow({ courseId, module }) {
  const [open, setOpen] = useState(false);
  const [pkg, setPkg] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && !pkg && !loading) {
      setLoading(true);
      fetchModulePackage(courseId, module.id)
        .then(setPkg)
        .catch((err) => setError(err.message))
        .finally(() => setLoading(false));
    }
  }

  return (
    <div className={`review-module ${open ? "is-open" : ""}`}>
      <button type="button" className="review-module__head" onClick={toggle} aria-expanded={open}>
        <span className="review-module__caret" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
        <strong>{module.title}</strong>
        <span className="mv-muted">
          {module.lesson_count} lesson{module.lesson_count === 1 ? "" : "s"} ·{" "}
          {module.track_count} track{module.track_count === 1 ? "" : "s"} ·{" "}
          {module.question_count} question{module.question_count === 1 ? "" : "s"}
        </span>
        {module.question_count === 0 && (
          <span className="review-tag">Review-only on mobile — no questions</span>
        )}
      </button>

      {open && (
        <div className="review-module__body">
          {loading && <div className="mv-muted">Loading packaged lesson…</div>}
          {error && <div className="error-banner">{error}</div>}
          {pkg &&
            pkg.lessons.map((lesson) => (
              <div key={lesson.id} className="review-lesson">
                <div className="review-lesson__title">{lesson.title}</div>

                <div className="review-lesson__section-label">
                  Narration tracks ({lesson.tracks.length})
                </div>
                {lesson.tracks.length === 0 ? (
                  <p className="mv-muted">No narration tracks generated yet.</p>
                ) : (
                  <ol className="review-track-list">
                    {lesson.tracks.map((track) => (
                      <li key={track.id}>
                        <div className="review-track-list__head">
                          <span>{track.title}</span>
                          <span
                            className={`review-audio-flag ${
                              track.audio_ready ? "is-ready" : ""
                            }`}
                          >
                            {track.audio_ready ? "audio ready" : "no audio yet"}
                          </span>
                        </div>
                        {track.text && (
                          <p className="review-track-list__text">{track.text}</p>
                        )}
                      </li>
                    ))}
                  </ol>
                )}

                <div className="review-lesson__section-label">
                  Questions ({lesson.questions.length})
                </div>
                {lesson.questions.length === 0 ? (
                  <p className="mv-muted">
                    No questions — the mobile app plays this lesson in review-only mode.
                  </p>
                ) : (
                  <ol className="review-question-list">
                    {lesson.questions.map((question) => (
                      <li key={question.id}>
                        <p className="review-question-list__prompt">{question.prompt}</p>
                        {Array.isArray(question.choices) && question.choices.length > 0 && (
                          <ul>
                            {question.choices.map((choice, index) => (
                              <li key={index}>{choice}</li>
                            ))}
                          </ul>
                        )}
                        <span className="review-question-list__answer">
                          Correct answer: {question.correct_answer || "—"}
                        </span>
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            ))}
        </div>
      )}
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
            <h3 className="mv-card__title">Packaged lessons</h3>
            <p className="mv-muted" style={{ marginTop: "-0.4rem", marginBottom: "1rem" }}>
              What the mobile app delivers to students, per module. Playback happens
              on mobile — this is the content, read-only.
            </p>
            {modules.length === 0 ? (
              <div className="empty-state">
                No modules with generated content yet. Publish topics and generate
                their lesson audio first.
              </div>
            ) : (
              <div className="review-module-list">
                {modules.map((module) => (
                  <ModuleRow key={module.id} courseId={courseId} module={module} />
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
