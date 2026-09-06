import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import TeacherShell from "./TeacherShell";
import { fetchModulePackage, markModuleReviewed } from "../../api";

function fmt(seconds) {
  if (!Number.isFinite(seconds)) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export default function ModulePlayerPage() {
  const { courseId, moduleId } = useParams();
  const audioRef = useRef(null);

  const [pkg, setPkg] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const [activeIndex, setActiveIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [finished, setFinished] = useState(false);
  const [reviewed, setReviewed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchModulePackage(courseId, moduleId)
      .then((data) => {
        if (!cancelled) setPkg(data);
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
  }, [courseId, moduleId]);

  // Flat track list across every lesson in the module, each track tagged with
  // its lesson so the playlist can group and the questions panel can scope.
  const tracks = useMemo(() => {
    if (!pkg) return [];
    const flat = [];
    pkg.lessons.forEach((lesson, lessonIndex) => {
      lesson.tracks.forEach((track) => {
        flat.push({ ...track, lessonIndex, lessonId: lesson.id, lessonTitle: lesson.title });
      });
    });
    return flat;
  }, [pkg]);

  const active = tracks[activeIndex];
  const activeLesson = pkg && active ? pkg.lessons[active.lessonIndex] : null;
  const anyQuestions = pkg ? pkg.has_questions : false;

  // Point the <audio> element at the active track whenever it changes.
  useEffect(() => {
    const el = audioRef.current;
    if (!el || !active) return;
    setCurrentTime(0);
    setDuration(0);
    if (active.audio_ready) {
      el.src = active.audio_url;
      el.load();
      if (isPlaying) el.play().catch(() => setIsPlaying(false));
    } else {
      el.removeAttribute("src");
      setIsPlaying(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIndex, active?.audio_url]);

  function togglePlay() {
    const el = audioRef.current;
    if (!el || !active?.audio_ready) return;
    if (isPlaying) {
      el.pause();
      setIsPlaying(false);
    } else {
      el.play().then(() => setIsPlaying(true)).catch(() => setIsPlaying(false));
    }
  }

  function go(index) {
    if (index < 0 || index >= tracks.length) return;
    setActiveIndex(index);
    setFinished(false);
  }

  function onEnded() {
    if (activeIndex + 1 < tracks.length) {
      setActiveIndex(activeIndex + 1);
    } else {
      setIsPlaying(false);
      setFinished(true);
    }
  }

  async function toggleReviewed() {
    try {
      const res = await markModuleReviewed(courseId, moduleId);
      setReviewed(res.reviewed);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <TeacherShell>
      <div className="mv-page-head">
        <h1>{pkg ? pkg.module.title : "Module"}</h1>
        <p>
          <Link to={`/review/courses/${courseId}`}>← Back to course</Link>
        </p>
      </div>

      {loading && <div className="empty-state">Loading module…</div>}
      {error && <div className="error-banner">{error}</div>}

      {!loading && pkg && tracks.length === 0 && (
        <div className="empty-state">
          This module has no generated audio tracks yet.
        </div>
      )}

      {!loading && pkg && tracks.length > 0 && (
        <div className="audiobook">
          <section className="mv-card audiobook__player">
            <div className="audiobook__art" aria-hidden="true">
              ♪
            </div>

            <div className="audiobook__now">
              <span className="audiobook__lesson">{active?.lessonTitle}</span>
              <strong className="audiobook__track">{active?.title}</strong>
              <span className="mv-muted">
                Track {activeIndex + 1} of {tracks.length}
              </span>
            </div>

            {!active?.audio_ready && (
              <div className="review-tag" style={{ alignSelf: "center" }}>
                Audio not generated for this track yet
              </div>
            )}

            <input
              className="audiobook__seek"
              type="range"
              min={0}
              max={duration || 0}
              step={0.1}
              value={currentTime}
              disabled={!active?.audio_ready}
              onChange={(event) => {
                const el = audioRef.current;
                if (el) {
                  el.currentTime = Number(event.target.value);
                  setCurrentTime(Number(event.target.value));
                }
              }}
              aria-label="Seek within track"
            />
            <div className="audiobook__times">
              <span>{fmt(currentTime)}</span>
              <span>{fmt(duration)}</span>
            </div>

            <div className="audiobook__transport">
              <button
                type="button"
                className="audiobook__btn"
                aria-label="Previous track"
                disabled={activeIndex === 0}
                onClick={() => go(activeIndex - 1)}
              >
                ⏮
              </button>
              <button
                type="button"
                className="audiobook__btn audiobook__btn--primary"
                aria-label={isPlaying ? "Pause" : "Play"}
                disabled={!active?.audio_ready}
                onClick={togglePlay}
              >
                {isPlaying ? "⏸" : "▶"}
              </button>
              <button
                type="button"
                className="audiobook__btn"
                aria-label="Next track"
                disabled={activeIndex + 1 >= tracks.length}
                onClick={() => go(activeIndex + 1)}
              >
                ⏭
              </button>
            </div>

            {finished && (
              <div className="audiobook__done">You’ve reached the end of this module.</div>
            )}

            <button
              type="button"
              className={`mv-btn ${reviewed ? "mv-btn--soft" : ""}`}
              onClick={toggleReviewed}
            >
              {reviewed ? "✓ Marked reviewed" : "Mark module reviewed"}
            </button>

            <audio
              ref={audioRef}
              onLoadedMetadata={(event) => setDuration(event.currentTarget.duration || 0)}
              onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
              onEnded={onEnded}
              onPlay={() => setIsPlaying(true)}
              onPause={() => setIsPlaying(false)}
            >
              <track kind="captions" />
            </audio>
          </section>

          <section className="mv-card audiobook__side">
            <h3 className="mv-card__title">Tracks</h3>
            <ol className="audiobook__playlist">
              {pkg.lessons.map((lesson, lessonIndex) => (
                <li key={lesson.id} className="audiobook__playlist-group">
                  <span className="audiobook__playlist-lesson">{lesson.title}</span>
                  {lesson.tracks.length === 0 && (
                    <span className="mv-muted"> — no tracks</span>
                  )}
                  {lesson.tracks.map((track) => {
                    const flatIndex = tracks.findIndex(
                      (t) => t.lessonIndex === lessonIndex && t.id === track.id
                    );
                    return (
                      <button
                        key={track.id}
                        type="button"
                        className={`audiobook__playlist-item ${
                          flatIndex === activeIndex ? "is-active" : ""
                        }`}
                        disabled={!track.audio_ready}
                        onClick={() => go(flatIndex)}
                      >
                        <span>{track.title}</span>
                        {!track.audio_ready && (
                          <span className="mv-muted">no audio yet</span>
                        )}
                      </button>
                    );
                  })}
                </li>
              ))}
            </ol>
          </section>

          <section className="mv-card audiobook__questions">
            <h3 className="mv-card__title">
              Questions{activeLesson ? ` — ${activeLesson.title}` : ""}
            </h3>

            {!anyQuestions ? (
              <div className="review-only-callout">
                <strong>No questions available for this module.</strong>
                <p>
                  Review-only mode: you can still play through every track and mark
                  the module reviewed. Students on the mobile app will simply listen
                  through this module without a quiz.
                </p>
              </div>
            ) : !activeLesson || activeLesson.questions.length === 0 ? (
              <p className="mv-muted">
                No questions for this lesson — the reviewer can continue.
              </p>
            ) : (
              <ol className="review-question-list">
                {activeLesson.questions.map((question) => (
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
          </section>
        </div>
      )}
    </TeacherShell>
  );
}
