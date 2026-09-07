import { Link } from "react-router-dom";
import AppShell from "../../components/AppShell";

const NAV = [
  { to: "/teacher", label: "Dashboard", icon: "▤", end: true },
  { to: "/courses", label: "Courses", icon: "▦" },
  { to: "/teacher/resources", label: "Resources", icon: "❐" },
  { to: "/teacher/settings", label: "Settings", icon: "⚙" },
];

export default function TeacherDashboard() {
  const rail = (
    <>
      <div className="mv-rail__card">
        <div className="mv-profile">
          <span className="mv-avatar">MK</span>
          <span>
            <span className="mv-profile__name">Maria Katrina Esclamado</span>
            <span className="mv-profile__role">Teacher</span>
          </span>
        </div>
        <p className="mv-rail__label">Progress</p>
        <span className="mv-chip">Grades</span>
        <span className="mv-chip">Lesson progress</span>
      </div>

      <div className="mv-rail__card">
        <p className="mv-rail__label">Upcoming tasks</p>
        <span className="mv-chip">Approve lesson</span>
        <span className="mv-chip">Create new course</span>
      </div>
    </>
  );

  return (
    <AppShell
      nav={NAV}
      rail={rail}
      primaryAction={{ to: "/courses/new", label: "＋ Add Course" }}
    >
      <div className="mv-page-head">
        <h1>What should we explore today?</h1>
        <p>Drop in a PDF and MAVIA will generate a learning quest for you.</p>
      </div>

      <section className="mv-card">
        <div className="mv-dropzone">
          <p style={{ fontSize: "1.4rem", marginBottom: ".5rem" }}>↓</p>
          <p>
            Drag a <strong>PDF</strong> of your choice and we&apos;ll{" "}
            <strong>generate a learning quest</strong> for you.
          </p>
        </div>
      </section>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.25rem" }}>
        <section className="mv-card">
          <h3 className="mv-card__title">To verify</h3>
          <div className="mv-list__item" style={{ flexDirection: "column", alignItems: "flex-start", gap: ".5rem" }}>
            <strong>Plants and Animals</strong>
            <span className="mv-muted" style={{ fontSize: ".85rem" }}>
              12 generated questions awaiting review
            </span>
            <button type="button" className="mv-btn mv-btn--soft">
              ▶ Review
            </button>
          </div>
        </section>

        <section className="mv-card">
          <h3 className="mv-card__title">Courses</h3>
          <p className="mv-muted" style={{ fontSize: ".9rem", marginBottom: ".9rem" }}>
            Create a course, upload its outline PDF, then map lesson PDFs to the
            extracted topics.
          </p>
          <Link to="/courses" className="mv-btn mv-btn--soft">
            Open Courses →
          </Link>
        </section>
      </div>

      <p className="mv-muted" style={{ fontSize: ".8rem" }}>
        Shaped for Touch, Heard to Learn.
      </p>
    </AppShell>
  );
}
