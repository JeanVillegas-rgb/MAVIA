// One arrow of the Course path, opened as a dialog: every concept link it
// carries, confirmed ones first and suggestions by shortlist rank, one
// full-width row each with its reason and the teacher's buttons. Nothing here
// changes links itself; decisions go back up to the page, which confirms them.
import { useEffect } from "react";

const STATUS_LABEL = { accepted: "Accepted", approved: "Approved by you", pending: "Suggestion" };
const byRank = (a, b) => (a.rank ?? Infinity) - (b.rank ?? Infinity) || a.id - b.id;

function Concept({ concept, topicTitle }) {
  return (
    <span className="cad-concept">
      <span className="cad-concept-title">{concept.title}</span>
      <span className="cad-concept-topic">{topicTitle(concept.topic_id)}</span>
    </span>
  );
}

function LinkRow({ link, topicTitle, onDecide }) {
  const pending = link.status === "pending";
  const pair = `${link.prerequisite.title} before ${link.dependent.title}`;
  return (
    <li className="cad-row">
      <div className="cad-pair">
        <Concept concept={link.prerequisite} topicTitle={topicTitle} />
        <span className="cad-pair-arrow" aria-hidden="true">→</span>
        <Concept concept={link.dependent} topicTitle={topicTitle} />
      </div>
      <p className="cad-reason">{link.reason}</p>
      <div className="cad-side">
        <span className="cad-status">
          {STATUS_LABEL[link.status] || link.status}
          {pending && link.rank ? `, closest match ${link.rank}` : ""}
        </span>
        <span className="cad-actions">
          {pending && (
            <button type="button" className="btn btn-small btn-primary" aria-label={`Approve ${pair}`}
                    onClick={() => onDecide(link, "approved")}>
              Approve
            </button>
          )}
          <button type="button" className="btn btn-small btn-secondary" aria-label={`${pending ? "Dismiss" : "Remove"} ${pair}`}
                  onClick={() => onDecide(link, "rejected")}>
            {pending ? "Dismiss" : "Remove"}
          </button>
        </span>
      </div>
    </li>
  );
}

function Group({ title, links, topicTitle, onDecide }) {
  if (!links.length) return null;
  return (
    <section className="cad-group" aria-label={title}>
      <h4>{title} ({links.length})</h4>
      <ul className="cad-list">
        {links.map((link) => <LinkRow key={link.id} link={link} topicTitle={topicTitle} onDecide={onDecide} />)}
      </ul>
    </section>
  );
}

export default function CourseArrowDialog({ arrow, topicTitle, onDecide, onClose, paused = false }) {
  useEffect(() => {
    // While a confirm dialog sits on top, Escape belongs to it.
    const onKey = (event) => event.key === "Escape" && !paused && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [paused, onClose]);

  const from = topicTitle(arrow.from_topic);
  const to = topicTitle(arrow.to_topic);
  const confirmed = arrow.links.filter((link) => link.status !== "pending").sort(byRank);
  const suggestions = arrow.links.filter((link) => link.status === "pending").sort(byRank);

  return (
    <div className="pg-dialog-backdrop cad-backdrop" onMouseDown={(event) => event.target === event.currentTarget && !paused && onClose()}>
      <section className="cad-dialog" role="dialog" aria-modal="true" aria-labelledby="cad-title">
        <header className="cad-head">
          <div>
            <h3 id="cad-title">{from} → {to}</h3>
            <p className="cad-summary">
              Concepts in “{to}” that build on concepts in “{from}”.
            </p>
          </div>
          <button type="button" className="btn btn-small btn-secondary" onClick={onClose}>Close</button>
        </header>
        {arrow.contradicts_outline && (
          <p className="cad-warning" role="note">
            These links run against your outline. To follow them, move “{from}” before “{to}” in the outline.
          </p>
        )}
        <div className="cad-body">
          <Group title="Confirmed" links={confirmed} topicTitle={topicTitle} onDecide={onDecide} />
          <Group title="To review" links={suggestions} topicTitle={topicTitle} onDecide={onDecide} />
        </div>
      </section>
    </div>
  );
}
