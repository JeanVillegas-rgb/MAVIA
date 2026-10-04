// Shown for ten seconds after each confirmed change. A failed undo stays up
// with its reason until dismissed.
import { useEffect } from "react";

export const UNDO_SECONDS = 10;

export default function UndoBar({ message, error, busy, onUndo, onClose }) {
  useEffect(() => {
    if (error) return undefined;
    const timer = setTimeout(onClose, UNDO_SECONDS * 1000);
    return () => clearTimeout(timer);
  }, [message, error, onClose]);

  return (
    <div className="pg-undo" role="status">
      <span>{error ? `Couldn't undo: ${error}. Refresh to see the current path.` : message}</span>
      {!error && (
        <button type="button" className="btn btn-small btn-secondary" disabled={busy} onClick={onUndo}>
          Undo
        </button>
      )}
      <button type="button" className="pg-undo-close" aria-label="Dismiss" onClick={onClose}>
        ×
      </button>
    </div>
  );
}
