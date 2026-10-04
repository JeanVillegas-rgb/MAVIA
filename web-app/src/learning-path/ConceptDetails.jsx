// The card a teacher reads after clicking a concept: its text, the files it
// came from, what must be learned before it, and -- on the editable screen --
// the recommended links still waiting for a decision. It sits over the
// graph's corner without a backdrop, so the highlighted neighbours stay visible.
import { useEffect } from "react";

export default function ConceptDetails({ step, editable = false, busy = false, onRemove, onAccept, onReject, onClose }) {
  useEffect(() => {
    if (!step) return undefined;
    const onKey = (event) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step, onClose]);

  if (!step) return null;
  const prerequisites = step.prerequisites || [];
  const pending = editable ? step.suggestions || [] : [];
  const sources = step.source_materials || [];

  return (
    <section className="pg-details" aria-labelledby="pg-details-title">
      <header className="pg-details-head">
        <h3 id="pg-details-title">
          {step.position}. {step.title || "Untitled concept"}
        </h3>
        <button type="button" className="btn btn-small btn-secondary" onClick={onClose}>
          Close
        </button>
      </header>
      {step.content && <p className="pg-details-text">{step.content}</p>}
      {sources.length > 0 && (
        <p className="pg-details-sources">From: {sources.map((source) => source.title).join(", ")}</p>
      )}
      <h4>Must learn first</h4>
      {prerequisites.length === 0 && pending.length === 0 && (
        <p className="muted-text">Nothing must be learned before this.</p>
      )}
      {prerequisites.length > 0 && (
        <ul className="pg-details-list">
          {prerequisites.map((link) => (
            <li key={link.link_id}>
              <div>
                <strong>{link.title}</strong>
                {link.reason && <small>{link.reason}</small>}
              </div>
              {editable && (
                <button type="button" className="btn btn-small btn-secondary" disabled={busy} onClick={() => onRemove(link, step)}>
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {pending.length > 0 && (
        <ul className="pg-pending-list" aria-label="Recommended links waiting for your decision">
          {pending.map((link) => (
            <li key={link.link_id} className="pg-pending">
              <div className="pg-pending-head">
                <span className="pg-pending-label">Pending</span>
                <strong>{link.title}</strong>
                {link.cross_section && (
                  <span
                    className="pg-flag"
                    title="The two concepts are under different lesson headings. In a hand-check, such suggestions were usually wrong."
                  >
                    different section
                  </span>
                )}
              </div>
              {link.reason && <small>{link.reason}</small>}
              <div className="pg-pending-actions">
                <button type="button" className="btn btn-small btn-primary" disabled={busy} onClick={() => onAccept(link, step)}>
                  Accept
                </button>
                <button type="button" className="btn btn-small btn-secondary" disabled={busy} onClick={() => onReject(link, step)}>
                  Reject
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
