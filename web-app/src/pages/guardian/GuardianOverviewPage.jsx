import { Link } from "react-router-dom";

import GuardianShell from "./GuardianShell";
import { child, recent, summary, topics } from "./guardianData";

// Mastery reads as three bands, and each one says so in words beside the bar:
// colour alone would not reach a guardian using a screen reader.
const BAR_COLOR = {
  finished: "var(--mv-success)",
  "in progress": "var(--mv-brand-500)",
  "needs practice": "var(--mv-warning)",
  "not started": "var(--mv-border-strong)",
};

export default function GuardianOverviewPage() {
  const rail = (
    <>
      <div className="mv-rail__card">
        <p className="mv-rail__label">Child</p>
        <div className="mv-profile">
          <div>
            <p className="mv-profile__name">{child.name}</p>
            <p className="mv-profile__role">
              {child.course} · {child.grade}
            </p>
          </div>
        </div>
        <p className="mv-muted" style={{ fontSize: ".85rem", marginTop: ".5rem" }}>
          Taught by {child.teacher}
        </p>
      </div>
      <div className="mv-rail__card">
        <p className="mv-rail__label">Recent lessons</p>
        {recent.map((item) => (
          <div key={item.lesson} style={{ marginBottom: ".7rem" }}>
            <p style={{ fontWeight: 600, fontSize: ".92rem" }}>{item.lesson}</p>
            <p className="mv-muted" style={{ fontSize: ".82rem" }}>
              {item.when} · {item.score}
            </p>
          </div>
        ))}
        <Link to="/guardian/scores" className="mv-btn mv-btn--soft mv-btn--block">
          See every score
        </Link>
      </div>
    </>
  );

  return (
    <GuardianShell rail={rail}>
      <div className="mv-page-head">
        <h1>How is {child.name.split(" ")[0]} doing?</h1>
        <p>
          {child.course} · {child.grade} · taught by {child.teacher}
        </p>
      </div>

      <section className="mv-stats">
        {summary.map((stat) => (
          <div key={stat.label} className="mv-stat">
            <div className="mv-stat__value">{stat.value}</div>
            <div className="mv-stat__label">{stat.label}</div>
            <p className="mv-muted" style={{ fontSize: ".82rem", marginTop: ".35rem" }}>
              {stat.note}
            </p>
          </div>
        ))}
      </section>

      <section className="mv-card">
        <h3 className="mv-card__title">Progress by topic</h3>
        <ul className="mv-list" style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {topics.map((topic) => (
            <li key={topic.title} className="mv-list__item" style={{ display: "block" }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: "1rem",
                  flexWrap: "wrap",
                  marginBottom: ".45rem",
                }}
              >
                <span style={{ fontWeight: 600 }}>{topic.title}</span>
                <span className="mv-muted">
                  {topic.mastery > 0 ? `${topic.mastery}% · ${topic.state}` : topic.state}
                </span>
              </div>
              <div
                className="mv-progress"
                role="img"
                aria-label={`${topic.title}: ${topic.mastery}% mastery, ${topic.state}`}
              >
                <div
                  className="mv-progress__fill"
                  style={{
                    width: `${topic.mastery}%`,
                    background: BAR_COLOR[topic.state],
                  }}
                />
              </div>
            </li>
          ))}
        </ul>
      </section>

    </GuardianShell>
  );
}
