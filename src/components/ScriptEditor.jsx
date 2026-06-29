import { useEffect, useState } from "react";

export default function ScriptEditor({ modules, onSave, onApprove, saving }) {
  const [drafts, setDrafts] = useState([]);

  useEffect(() => {
    setDrafts(
      modules.map((module) => ({
        id: module.id,
        title: module.title,
        narrative_text: module.narrative_text,
      }))
    );
  }, [modules]);

  function updateModule(index, field, value) {
    setDrafts((current) =>
      current.map((item, itemIndex) =>
        itemIndex === index ? { ...item, [field]: value } : item
      )
    );
  }

  return (
    <section className="card" style={{ marginTop: "1.25rem" }}>
      <h3>Review narration script</h3>
      <p style={{ color: "var(--muted)", marginBottom: "1rem" }}>
        Edit the generated story script before audio is created. When you are satisfied, approve
        the script to start audiobook generation.
      </p>

      <div className="script-editor">
        {drafts.map((module, index) => (
          <article key={module.id} className="script-module-card">
            <div className="field">
              <label htmlFor={`title-${module.id}`}>Chapter title</label>
              <input
                id={`title-${module.id}`}
                type="text"
                value={module.title}
                onChange={(event) => updateModule(index, "title", event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor={`text-${module.id}`}>Narration script</label>
              <textarea
                id={`text-${module.id}`}
                rows={8}
                value={module.narrative_text}
                onChange={(event) => updateModule(index, "narrative_text", event.target.value)}
              />
            </div>
          </article>
        ))}
      </div>

      <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", marginTop: "1rem" }}>
        <button
          className="btn btn-secondary"
          type="button"
          disabled={saving}
          onClick={() => onSave(drafts)}
        >
          {saving ? "Saving..." : "Save script changes"}
        </button>
        <button
          className="btn btn-primary"
          type="button"
          disabled={saving}
          onClick={() => onApprove(drafts)}
        >
          Approve script & generate audio
        </button>
      </div>
    </section>
  );
}
