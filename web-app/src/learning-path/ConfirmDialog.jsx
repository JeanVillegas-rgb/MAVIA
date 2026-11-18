// Every change on the learning path is confirmed here first. A refusal from
// the server (a loop, a link that no longer exists) is shown in the dialog,
// which stays open so the teacher can read it.
import { useEffect } from "react";

// `order` draws the two concepts top to bottom, so the dialog shows which one
// is learned first instead of saying it in a sentence with both names in it.
export default function ConfirmDialog({ title, order, message, actions = [], cancelLabel = "Cancel", error, busy, onCancel }) {
  useEffect(() => {
    const onKey = (event) => event.key === "Escape" && !busy && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  return (
    <div className="pg-dialog-backdrop">
      <section className="pg-dialog" role="alertdialog" aria-modal="true" aria-labelledby="pg-confirm-title">
        <h3 id="pg-confirm-title">{title}</h3>
        {order && (
          <div className="pg-order">
            <div className="pg-order-row">
              <small>Learn first</small>
              <strong>{order.first}</strong>
            </div>
            <span className="pg-order-arrow" aria-hidden="true">↓</span>
            <div className="pg-order-row">
              <small>Then</small>
              <strong>{order.then}</strong>
            </div>
          </div>
        )}
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
