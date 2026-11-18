import { useState } from "react";

// Move one object only; its companions and source PDF stay unchanged.
export default function ConceptGroupingControls({ item, group, groups, busy, onCorrect }) {
  const [destination, setDestination] = useState("");
  const [label, setLabel] = useState(item.title || "");
  const targets = groups.filter((candidate) => candidate.id !== group.id);
  return (
    <details className="concept-grouping-controls">
      <summary>Correct this object's concept</summary>
      <p>Changes only this object, not the whole PDF. This does not split paragraphs.</p>
      <fieldset disabled={busy}>
        <form onSubmit={(event) => {
          event.preventDefault();
          if (label.trim()) onCorrect(item.id, { label: label.trim() });
        }}>
          <label>New concept name
            <input value={label} maxLength={255} required
              onChange={(event) => setLabel(event.target.value)} />
          </label>
          <button type="submit" className="btn btn-secondary" disabled={!label.trim()}>
            Separate into own concept
          </button>
        </form>
        {targets.length > 0 && (
          <form onSubmit={(event) => {
            event.preventDefault();
            if (destination) onCorrect(item.id, { targetGroupId: destination });
          }}>
            <label>Existing concept
              <select value={destination} required onChange={(event) => setDestination(event.target.value)}>
                <option value="">Choose destination...</option>
                {targets.map((target) => (
                  <option key={target.id} value={target.id}>
                    {target.display_title || target.label || "Untitled concept"} (#{target.id})
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" className="btn btn-secondary" disabled={!destination}>
              Move to concept
            </button>
          </form>
        )}
      </fieldset>
    </details>
  );
}
