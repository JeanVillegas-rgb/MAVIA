import { useMemo, useRef } from "react";

function formatDuration(seconds) {
  if (!seconds) {
    return "—";
  }
  const total = Math.round(seconds);
  const minutes = Math.floor(total / 60);
  const remaining = total % 60;
  return `${minutes}:${String(remaining).padStart(2, "0")}`;
}

export default function AudiobookPlayer({ modules, activeIndex, onActiveIndexChange }) {
  const audioRef = useRef(null);
  const activeModule = modules[activeIndex];

  const playlistLabel = useMemo(() => {
    if (!modules.length) {
      return "No modules yet";
    }
    return `Module ${activeIndex + 1} of ${modules.length}`;
  }, [activeIndex, modules.length]);

  function playNext() {
    onActiveIndexChange(Math.min(activeIndex + 1, modules.length - 1));
  }

  return (
    <div className="player-panel card">
      <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem" }}>
        <div>
          <h3>{activeModule?.title || "Audiobook player"}</h3>
          <p style={{ color: "var(--muted)", marginTop: "0.35rem" }}>{playlistLabel}</p>
        </div>
        <span className="status-pill status-completed">
          {formatDuration(activeModule?.duration_seconds)}
        </span>
      </div>

      {activeModule?.audio_url ? (
        <audio
          ref={audioRef}
          key={activeModule.id}
          controls
          autoPlay
          src={activeModule.audio_url}
          onEnded={playNext}
        />
      ) : (
        <div className="empty-state">Audio is still being generated.</div>
      )}

      {activeModule?.narrative_text && (
        <div className="narrative-box">{activeModule.narrative_text}</div>
      )}
    </div>
  );
}

export function ModuleSidebar({ modules, activeIndex, onSelect }) {
  if (!modules.length) {
    return <div className="empty-state">Chapters will appear here after processing.</div>;
  }

  return (
    <div className="module-list">
      {modules.map((module, index) => (
        <button
          key={module.id}
          type="button"
          className={`module-button ${index === activeIndex ? "active" : ""}`}
          onClick={() => onSelect(index)}
        >
          <strong>
            {module.order}. {module.title}
          </strong>
          <span>{formatDuration(module.duration_seconds)}</span>
        </button>
      ))}
    </div>
  );
}

export { formatDuration };
