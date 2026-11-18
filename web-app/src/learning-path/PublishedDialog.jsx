// Shown after a successful publish: the path a learner will now walk, each
// concept's Standard narration playable in teaching order, one player for each
// learning object the concept is taught as.
import { useCallback, useEffect, useRef, useState } from "react";

import { fetchPublishedPath } from "../api";
import { publishedClips } from "./publishedAudio";
import "./pathGraph.css";

// One player per learning object of the concept's Standard version, each
// labelled with the object it narrates, so every part can be played and
// checked on its own.
function ConceptPlayers({ parts, title, onPlay }) {
  return (
    <div className="pub-parts">
      {parts.map((part, index) => (
        <div className="pub-player" key={`${part.url}-${index}`}>
          {parts.length > 1 && (
            <small className="pub-part-label">
              {index + 1}. {part.title || `Part ${index + 1}`}
            </small>
          )}
          <audio
            controls
            preload="none"
            src={part.url}
            aria-label={`Narration for ${title}${parts.length > 1 ? `, ${part.title || `part ${index + 1}`}` : ""}`}
            onPlay={(event) => onPlay(event.currentTarget)}
          />
        </div>
      ))}
    </div>
  );
}

export default function PublishedDialog({ topicId, topicTitle, onClose }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const playing = useRef(null);

  useEffect(() => {
    let cancelled = false;
    fetchPublishedPath(topicId)
      .then((data) => !cancelled && setRows(publishedClips(data)))
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [topicId]);

  useEffect(() => {
    const onKey = (event) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // One narration at a time: starting a concept pauses whichever was playing.
  const onPlay = useCallback((element) => {
    if (playing.current && playing.current !== element) playing.current.pause();
    playing.current = element;
  }, []);

  const clipCount = (rows || []).reduce((count, row) => count + row.clips.length, 0);

  return (
    <div className="pg-dialog-backdrop">
      <section className="pub-dialog" role="dialog" aria-modal="true" aria-labelledby="pub-title">
        <header className="pub-head">
          <span className="pub-check" aria-hidden="true">✓</span>
          <div>
            <h3 id="pub-title">Topic published!</h3>
            <p className="pub-topic">{topicTitle}</p>
            {rows && (
              <p className="pub-meta">
                {rows.length} concept{rows.length === 1 ? "" : "s"} · {clipCount} audio clip{clipCount === 1 ? "" : "s"}
              </p>
            )}
          </div>
        </header>

        <div className="pub-body">
          {error && <p className="pg-dialog-error" role="alert">Couldn't load the published path: {error}</p>}
          {!rows && !error && <p className="muted-text">Loading the published path…</p>}
          {rows && (
            <ol className="pub-list">
              {rows.map((row) => (
                <li key={row.position} className="pub-row">
                  <span className="pub-position">{row.position}</span>
                  <div className="pub-row-main">
                    <strong>{row.title}</strong>
                    {row.clips.length > 0 ? (
                      <ConceptPlayers parts={row.parts} title={row.title} onPlay={onPlay} />
                    ) : (
                      <p className="pub-missing">No audio for this concept.</p>
                    )}
                    {row.clips.length > 0 && row.missing > 0 && (
                      <p className="pub-missing">
                        {row.missing} part{row.missing === 1 ? " has" : "s have"} no audio and {row.missing === 1 ? "is" : "are"} skipped.
                      </p>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>

        <footer className="pub-foot">
          <button type="button" className="btn btn-primary" onClick={onClose}>
            Close
          </button>
        </footer>
      </section>
    </div>
  );
}
