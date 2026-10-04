// Every change on the learning path is confirmed here first. A refusal from
// the server (a loop, a link that no longer exists) is shown in the dialog,
// which stays open so the teacher can read it.
import { useEffect } from "react";

export default function ConfirmDialog({ title, message, actions = [], cancelLabel = "Cancel", error, busy, onCancel }) {
  useEffect(() => {
    const onKey = (event) => event.key === "Escape" && !busy && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  return (
    <div className="pg-dialog-backdrop">
      <section className="pg-dialog" role="alertdialog" aria-modal="true" aria-labelledby="pg-confirm-title">
        <h3 id="pg-confirm-title">{title}</h3>
        {message && <p>{message}</p>}
        {error && <p className="pg-dialog-error" role="alert">{error}</p>}
        <div className="pg-dialog-actions">
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={onCancel}>
            {cancelLabel}
          </button>
          {actions.map((action) => (
            <button
              key={action.label}
              type="button"
              className={`btn ${action.primary ? "btn-primary" : "btn-secondary"}`}
              disabled={busy}
              onClick={action.onClick}
            >
              {action.label}
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
