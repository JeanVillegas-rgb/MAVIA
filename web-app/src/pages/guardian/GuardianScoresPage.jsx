import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import GuardianShell from "./GuardianShell";
import { fetchChild, fetchScores } from "./api/guardianApi";

const RESULT_PILL = {
  Mastered: "mv-pill mv-pill--ok",
  "Needs practice": "mv-pill mv-pill--pending",
  Learning: "mv-pill mv-pill--student",
};

export default function GuardianScoresPage() {
  // Both come through api/guardianApi.js -- see the note there.
  const [child, setChild] = useState(null);
  const [scores, setScores] = useState([]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchChild(), fetchScores()]).then(([childData, scoreData]) => {
      if (cancelled) return;
      setChild(childData);
      setScores(scoreData);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!child) {
    return (
      <GuardianShell>
        <div className="mv-page-head">
          <h1>Loading&hellip;</h1>
        </div>
      </GuardianShell>
    );
  }

  const firstName = child.name.split(" ")[0];

  return (
    <GuardianShell>
      <div className="mv-page-head">
        <Link to="/guardian" className="mv-muted" style={{ fontSize: ".9rem" }}>
          Back to overview
        </Link>
        <h1>{firstName}&rsquo;s scores</h1>
        <p>Every lesson {firstName} has answered, newest first.</p>
      </div>

      <section className="mv-card">
        <div style={{ overflowX: "auto" }}>
          <table className="mv-table">
            <thead>
              <tr>
                <th>Lesson</th>
                <th>Topic</th>
                <th>Date</th>
                <th>Score</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {scores.map((row) => (
                <tr key={`${row.lesson}-${row.date}`}>
                  <td style={{ fontWeight: 600 }}>{row.lesson}</td>
                  <td className="mv-muted">{row.topic}</td>
                  <td className="mv-muted">{row.date}</td>
                  <td>{row.score}</td>
                  <td>
                    <span className={RESULT_PILL[row.result] || "mv-pill"}>{row.result}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </GuardianShell>
  );
}
