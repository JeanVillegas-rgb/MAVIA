import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import AudiobookPlayer, { ModuleSidebar } from "../components/AudiobookPlayer";
import ScriptEditor from "../components/ScriptEditor";
import {
  approveLessonScript,
  deleteLesson,
  fetchLesson,
  publishLesson,
  reprocessLesson,
  updateLessonScript,
} from "../api";

const PROCESSING_STATUSES = new Set(["processing", "audio_generating", "script_approved"]);

export default function LessonDetailPage() {
  const { id } = useParams();
  const [searchParams] = useSearchParams();
  const isStudentView = searchParams.get("student") === "1";

  const [lesson, setLesson] = useState(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setActiveIndex(0);
  }, [id, lesson?.audio_modules?.length]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await fetchLesson(id);
        if (!cancelled) {
          setLesson(data);
          setError("");
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    }

    load();
    const interval = setInterval(load, 3000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [id]);

  async function handleSaveScript(modules) {
    setBusy(true);
    setError("");
    try {
      const data = await updateLessonScript(id, modules);
      setLesson(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleApproveScript(modules) {
    setBusy(true);
    setError("");
    try {
      await updateLessonScript(id, modules);
      const data = await approveLessonScript(id);
      setLesson(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handlePublish() {
    setBusy(true);
    setError("");
    try {
      const data = await publishLesson(id);
      setLesson(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleReprocess() {
    setBusy(true);
    try {
      await reprocessLesson(id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!window.confirm("Delete this lesson and all generated content?")) return;
    setBusy(true);
    try {
      await deleteLesson(id);
      window.location.href = lesson?.course_id ? `/courses/${lesson.course_id}` : "/";
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  if (error && !lesson) {
    return (
      <div className="card">
        <div className="error-banner">{error}</div>
        <Link to="/" className="btn btn-secondary" style={{ display: "inline-block", marginTop: "1rem" }}>
          Back home
        </Link>
      </div>
    );
  }

  if (!lesson) return <div className="empty-state">Loading lesson...</div>;

  if (isStudentView && lesson.status !== "published") {
    return (
      <div className="card">
        <div className="error-banner">This lesson is not published yet.</div>
        <Link to={`/courses/${lesson.course_id}`} className="btn btn-secondary" style={{ display: "inline-block", marginTop: "1rem" }}>
          Back to course
        </Link>
      </div>
    );
  }

  const modules = lesson.audio_modules || [];
  const showScriptEditor = !isStudentView && lesson.status === "script_review";
  const showAudioPlayer =
    lesson.status === "audio_review" ||
    lesson.status === "published" ||
    (isStudentView && lesson.status === "published");
  const showProcessing = PROCESSING_STATUSES.has(lesson.status);

  return (
    <>
      <section className="card" style={{ marginBottom: "1.25rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem", flexWrap: "wrap" }}>
          <div>
            {lesson.course_id ? (
              <Link to={`/courses/${lesson.course_id}`} style={{ color: "var(--muted)" }}>
                ← Back to course
              </Link>
            ) : (
              <Link to="/" style={{ color: "var(--muted)" }}>
                ← Back home
              </Link>
            )}
            <h2 style={{ marginTop: "0.75rem" }}>{lesson.title}</h2>
            {lesson.story_intro && (
              <p style={{ color: "var(--muted)", maxWidth: "70ch", marginTop: "0.75rem" }}>
                {lesson.story_intro}
              </p>
            )}
          </div>
          {!isStudentView && (
            <div style={{ display: "flex", gap: "0.75rem", alignItems: "start", flexWrap: "wrap" }}>
              <span className={`status-pill status-${lesson.status}`}>{lesson.status.replaceAll("_", " ")}</span>
              {lesson.status === "failed" && (
                <button className="btn btn-secondary" type="button" onClick={handleReprocess} disabled={busy}>
                  Reprocess PDF
                </button>
              )}
              {lesson.status === "audio_review" && (
                <button className="btn btn-primary" type="button" onClick={handlePublish} disabled={busy}>
                  Publish to course group
                </button>
              )}
              <button className="btn btn-secondary" type="button" onClick={handleDelete} disabled={busy}>
                Delete
              </button>
            </div>
          )}
        </div>

        {showProcessing && (
          <div style={{ marginTop: "1rem" }}>
            <div className="progress-bar">
              <span style={{ width: `${lesson.progress}%` }} />
            </div>
            <p style={{ color: "var(--muted)", marginTop: "0.5rem" }}>
              {lesson.status === "processing" && "Extracting PDF, interpreting images, and writing the narration script..."}
              {lesson.status === "script_approved" && "Script approved. Preparing audio generation..."}
              {lesson.status === "audio_generating" && "Generating audiobook audio from the approved script..."}
            </p>
          </div>
        )}

        {error && <div className="error-banner">{error}</div>}
        {lesson.error_message && <div className="error-banner">{lesson.error_message}</div>}
      </section>

      {showScriptEditor && (
        <ScriptEditor
          modules={modules}
          onSave={handleSaveScript}
          onApprove={handleApproveScript}
          saving={busy}
        />
      )}

      {showAudioPlayer && modules.length > 0 && (
        <section className="player-layout">
          <div className="card">
            <h3>Chapters</h3>
            <ModuleSidebar modules={modules} activeIndex={activeIndex} onSelect={setActiveIndex} />
          </div>
          <AudiobookPlayer
            modules={modules}
            activeIndex={activeIndex}
            onActiveIndexChange={setActiveIndex}
          />
        </section>
      )}

      {!isStudentView && lesson.pages?.length > 0 && (
        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3>Extracted lesson content</h3>
          <div className="page-grid">
            {lesson.pages.map((page) => (
              <article key={page.id} className="page-card">
                <strong>Page {page.page_number}</strong>
                {page.raw_text && (
                  <p style={{ whiteSpace: "pre-wrap", color: "var(--muted)" }}>{page.raw_text}</p>
                )}
                {page.images?.map((image) => (
                  <div key={image.id}>
                    {image.image_url && (
                      <img src={image.image_url} alt={image.caption || "Lesson illustration"} />
                    )}
                    {image.interpretation && (
                      <p style={{ color: "var(--muted)" }}>
                        <em>{image.interpretation}</em>
                      </p>
                    )}
                  </div>
                ))}
              </article>
            ))}
          </div>
        </section>
      )}
    </>
  );
}
