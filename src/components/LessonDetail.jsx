import { useEffect, useState } from "react";
import AudioPlayer from "./AudioPlayer";
import { ModuleList } from "./LessonList";
import { reprocessLesson, statusLabel } from "../api";

export default function LessonDetail({ lesson, onRefresh }) {
  const [activeModuleId, setActiveModuleId] = useState(null);
  const [reprocessing, setReprocessing] = useState(false);

  useEffect(() => {
    if (lesson?.modules?.length && !activeModuleId) {
      setActiveModuleId(lesson.modules[0].id);
    }
  }, [lesson, activeModuleId]);

  if (!lesson) {
    return (
      <section className="panel detail-panel placeholder">
        <div className="placeholder-inner">
          <span className="orbit-icon">🪐</span>
          <h2>Select a lesson</h2>
          <p className="muted">
            Choose a lesson from your library or upload a new science PDF to
            begin listening.
          </p>
        </div>
      </section>
    );
  }

  const activeModule = lesson.modules?.find((m) => m.id === activeModuleId);
  const isProcessing =
    lesson.status === "pending" || lesson.status === "processing";

  const handleReprocess = async () => {
    setReprocessing(true);
    try {
      await reprocessLesson(lesson.id);
      onRefresh?.();
    } finally {
      setReprocessing(false);
    }
  };

  const playNextModule = () => {
    const idx = lesson.modules.findIndex((m) => m.id === activeModuleId);
    if (idx >= 0 && idx < lesson.modules.length - 1) {
      setActiveModuleId(lesson.modules[idx + 1].id);
    }
  };

  return (
    <section className="panel detail-panel">
      <header className="detail-header">
        <div>
          <p className="eyebrow">Science story audiobook</p>
          <h2>{lesson.title}</h2>
          <p className="muted">
            {lesson.page_count} pages · {lesson.modules?.length || 0} audio
            modules
          </p>
        </div>
        <span className={`status-pill status-${lesson.status}`}>
          {statusLabel(lesson.status)}
        </span>
      </header>

      {isProcessing && (
        <div className="processing-banner">
          <div className="spinner" />
          <div>
            <strong>Building your story…</strong>
            <p className="muted">
              Extracting text, interpreting images, writing narrative, and
              synthesizing speech. This page refreshes automatically.
            </p>
          </div>
        </div>
      )}

      {lesson.status === "failed" && (
        <div className="error-banner">
          <strong>Something went wrong</strong>
          <p>{lesson.error_message || "Processing failed."}</p>
          <button
            type="button"
            className="secondary-btn"
            onClick={handleReprocess}
            disabled={reprocessing}
          >
            {reprocessing ? "Retrying…" : "Try again"}
          </button>
        </div>
      )}

      {lesson.status === "completed" && activeModule && (
        <>
          <div className="now-playing">
            <AudioPlayer
              src={activeModule.audio_url}
              title={`${activeModule.chapter_label}: ${activeModule.title}`}
              onEnded={playNextModule}
            />
          </div>

          <div className="detail-grid">
            <ModuleList
              modules={lesson.modules}
              activeModuleId={activeModuleId}
              onSelectModule={setActiveModuleId}
            />

            <div className="script-panel">
              <h3>Story script</h3>
              <div className="script-text">{activeModule.narrative_script}</div>
            </div>
          </div>
        </>
      )}

      {lesson.images?.length > 0 && (
        <div className="images-section">
          <h3>Interpreted visuals</h3>
          <div className="image-grid">
            {lesson.images.map((img) => (
              <article key={img.id} className="image-card">
                <img src={img.image_url} alt={img.caption} loading="lazy" />
                <div>
                  <strong>Page {img.page_number}</strong>
                  <p>{img.interpretation}</p>
                </div>
              </article>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
