import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import TeacherShell from "./TeacherShell";
import { fetchCourse, fetchModulePackage, fetchReviewModules } from "../../api";

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
          <span className="review-tag">No questions</span>
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
                    No detected questions for this lesson.
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
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    Promise.all([fetchCourse(courseId), fetchReviewModules(courseId)])
      .then(([courseData, moduleData]) => {
        if (!cancelled) { setCourse(courseData); setModules(moduleData); }
      })
      .catch(err => { if (!cancelled) setError(err.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [courseId]);
  return <TeacherShell>
    <div className="mv-page-head"><h1>{course?.title || "Course review"}</h1>
      <Link to="/review">Back to all courses</Link></div>
    {loading && <p role="status">Loading lessons...</p>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!loading && !error && <section className="mv-card">
      <h2 className="mv-card__title">Lesson content</h2>
      <p className="mv-muted">Read-only teacher preview of lesson narration and detected questions. This does not change student progress or the existing adaptive delivery system.</p>
      {modules.length ? modules.map(module => <ModuleRow key={module.id} courseId={courseId} module={module} />) : <p>No modules yet. Add a course outline and learning materials first.</p>}
    </section>}
    <section className="mv-card"><h2 className="mv-card__title">Student progress</h2>
      <p>The new roster and progress dashboard is not connected yet. Your existing adaptive system and its saved progress are preserved.</p>
    </section>
  </TeacherShell>;
}
