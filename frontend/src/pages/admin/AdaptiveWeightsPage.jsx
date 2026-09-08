import { useEffect, useState } from "react";
import AppShell from "../../components/AppShell";
import {
  fetchAdaptiveConfig,
  resetAdaptiveConfig,
  updateAdaptiveConfig,
} from "../../api";

const NAV = [
  { to: "/admin", label: "Overview", icon: "▤", end: true },
  { to: "/courses", label: "Courses", icon: "▦" },
  { to: "/admin/adaptive-weights", label: "Adaptive weights", icon: "⚖" },
];

const TRACING_FIELDS = [
  {
    key: "p_guess",
    label: "P(guess)",
    help: "Chance a learner answers correctly without actually knowing the material. Higher means a correct answer earns less credit.",
  },
  {
    key: "p_slip",
    label: "P(slip)",
    help: "Chance a learner who knows the material still answers wrong. Higher means a wrong answer costs less mastery.",
  },
  {
    key: "p_learn",
    label: "P(learn)",
    help: "How much a single question moves a learner from “doesn’t know” toward “knows,” right or wrong. Higher means mastery climbs faster.",
  },
];

const MASTERY_FIELDS = [
  {
    key: "starting_mastery",
    label: "Starting mastery",
    help: "Starting mastery for a new enrollment-based learning state. Existing states are unchanged.",
  },
  {
    key: "mastery_ceiling",
    label: "Mastery ceiling",
    help: "Upper cap on computed mastery — keeps the score short of claiming full certainty.",
  },
];

function SliderField({ field, value, onChange, disabled }) {
  return (
    <div className="mv-slider-field">
      <div className="mv-slider-field__head">
        <span className="mv-slider-field__label">{field.label}</span>
        <span className="mv-slider-field__value">{Number(value).toFixed(2)}</span>
      </div>
      <input
        type="range"
        className="mv-slider"
        min={0}
        max={1}
        step={0.01}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(field.key, Number(event.target.value))}
      />
      <p className="mv-slider-field__help">{field.help}</p>
    </div>
  );
}

export default function AdaptiveWeightsPage() {
  const [form, setForm] = useState(null);
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const data = await fetchAdaptiveConfig();
      applyConfig(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function applyConfig(data) {
    const { updated_at, updated_by_username, ...weights } = data;
    setForm(weights);
    setMeta({ updated_at, updated_by_username });
  }

  function update(key, value) {
    setSaved(false);
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSave(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      const data = await updateAdaptiveConfig(form);
      applyConfig(data);
      setSaved(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleReset() {
    setResetting(true);
    setError("");
    setSaved(false);
    try {
      const data = await resetAdaptiveConfig();
      applyConfig(data);
      setSaved(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setResetting(false);
    }
  }

  return (
    <AppShell nav={NAV}>
      <div className="mv-page-head">
        <h1>Adaptive engine weights</h1>
        <p>
          These numbers drive the Bayesian knowledge-tracing model every
          learner&apos;s lesson sequencing runs on. Changes apply to every
          learner immediately &mdash; there&apos;s no per-course override.
        </p>
      </div>

      {loading ? (
        <section className="mv-card">
          <p className="mv-muted">Loading current weights…</p>
        </section>
      ) : (
        <form onSubmit={handleSave}>
          <section className="mv-card">
            <h3 className="mv-card__title">Knowledge tracing</h3>
            {TRACING_FIELDS.map((field) => (
              <SliderField
                key={field.key}
                field={field}
                value={form[field.key]}
                onChange={update}
                disabled={saving || resetting}
              />
            ))}
          </section>

          <section className="mv-card" style={{ marginTop: "1.25rem" }}>
            <h3 className="mv-card__title">Mastery range</h3>
            {MASTERY_FIELDS.map((field) => (
              <SliderField
                key={field.key}
                field={field}
                value={form[field.key]}
                onChange={update}
                disabled={saving || resetting}
              />
            ))}
          </section>

          <section className="mv-card" style={{ marginTop: "1.25rem" }}>
            <h3 className="mv-card__title">Question difficulty</h3>
            <div className="mv-field">
              <label htmlFor="default-difficulty">Default difficulty</label>
              <select
                id="default-difficulty"
                value={form.default_difficulty}
                disabled
                onChange={(event) => update("default_difficulty", event.target.value)}
              >
                <option value="easy">Easy</option>
                <option value="medium">Medium</option>
                <option value="hard">Hard</option>
              </select>
            </div>
            <p className="mv-slider-field__help">
              Reserved for future difficulty-based selection. The enrollment engine
              currently follows question order; this setting is not applied.
            </p>
          </section>

          {error && (
            <div className="mv-alert" style={{ marginTop: "1.25rem" }}>
              {error}
            </div>
          )}
          {saved && !error && (
            <div
              className="mv-alert"
              style={{
                marginTop: "1.25rem",
                background: "var(--mv-success)",
                color: "#fff",
                borderColor: "var(--mv-success)",
              }}
            >
              Saved.
            </div>
          )}

          <div
            style={{
              marginTop: "1.25rem",
              display: "flex",
              alignItems: "center",
              gap: "0.75rem",
              flexWrap: "wrap",
            }}
          >
            <button
              type="submit"
              className="mv-btn"
              disabled={saving || resetting}
            >
              {saving ? "Saving…" : "Save changes"}
            </button>
            <button
              type="button"
              className="mv-btn mv-btn--ghost"
              disabled={saving || resetting}
              onClick={handleReset}
            >
              {resetting ? "Resetting…" : "Reset to defaults"}
            </button>
            {meta?.updated_at && (
              <span className="mv-muted" style={{ fontSize: ".82rem" }}>
                Last updated {new Date(meta.updated_at).toLocaleString()}
                {meta.updated_by_username ? ` by ${meta.updated_by_username}` : ""}
              </span>
            )}
          </div>
        </form>
      )}
    </AppShell>
  );
}
