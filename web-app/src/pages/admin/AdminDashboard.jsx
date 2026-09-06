import { Link } from "react-router-dom";
import AppShell from "../../components/AppShell";

const NAV = [
  { to: "/admin", label: "Overview", icon: "▤", end: true },
  { to: "/admin/users", label: "Users", icon: "◍" },
  { to: "/admin/courses", label: "Courses", icon: "▦" },
  { to: "/admin/adaptive-weights", label: "Adaptive weights", icon: "⚖" },
  { to: "/admin/settings", label: "Settings", icon: "⚙" },
];

const STATS = [
  { value: "128", label: "Students" },
  { value: "14", label: "Teachers" },
  { value: "9", label: "Courses" },
  { value: "3", label: "Pending approvals" },
];

const USERS = [
  { name: "Maria June Kathleen", username: "mjkathleen", role: "TEACHER", status: "ok" },
  { name: "Liam Ortega", username: "liam.o", role: "STUDENT", status: "ok" },
  { name: "Grace Villanueva", username: "gracev", role: "TEACHER", status: "pending" },
  { name: "Noah Santos", username: "noah.s", role: "STUDENT", status: "ok" },
  { name: "Priya Nair", username: "priya.n", role: "ADMIN", status: "ok" },
];

const ROLE_PILL = {
  STUDENT: "mv-pill mv-pill--student",
  TEACHER: "mv-pill mv-pill--teacher",
  ADMIN: "mv-pill mv-pill--admin",
};

export default function AdminDashboard() {
  const rail = (
    <>
      <div className="mv-rail__card">
        <p className="mv-rail__label">Needs attention</p>
        <span className="mv-chip">3 teacher accounts to approve</span>
        <span className="mv-chip">1 course flagged</span>
      </div>
      <div className="mv-rail__card">
        <p className="mv-rail__label">Quick actions</p>
        <span className="mv-chip">Invite a user</span>
        <span className="mv-chip">Export roster</span>
      </div>
    </>
  );

  return (
    <AppShell
      nav={NAV}
      rail={rail}
      primaryAction={{ to: "/admin/users/new", label: "＋ Add user" }}
    >
      <div className="mv-page-head">
        <h1>Admin overview</h1>
        <p>Manage accounts, roles, and access across MAVIA.</p>
      </div>

      <section className="mv-stats">
        {STATS.map((stat) => (
          <div key={stat.label} className="mv-stat">
            <div className="mv-stat__value">{stat.value}</div>
            <div className="mv-stat__label">{stat.label}</div>
          </div>
        ))}
      </section>

      <section className="mv-card" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "1rem", flexWrap: "wrap" }}>
        <div>
          <h3 className="mv-card__title" style={{ marginBottom: ".25rem" }}>Adaptive engine weights</h3>
          <p className="mv-muted" style={{ fontSize: ".88rem" }}>
            Tune the knowledge-tracing model every learner's lesson sequencing runs on.
          </p>
        </div>
        <Link to="/admin/adaptive-weights" className="mv-btn mv-btn--soft">
          Open
        </Link>
      </section>

      <section className="mv-card">
        <h3 className="mv-card__title">Recent users</h3>
        <div style={{ overflowX: "auto" }}>
          <table className="mv-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Username</th>
                <th>Role</th>
                <th>Status</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {USERS.map((user) => (
                <tr key={user.username}>
                  <td>{user.name}</td>
                  <td className="mv-muted">@{user.username}</td>
                  <td>
                    <span className={ROLE_PILL[user.role]}>{user.role}</span>
                  </td>
                  <td>
                    <span
                      className={
                        user.status === "pending"
                          ? "mv-pill mv-pill--pending"
                          : "mv-pill mv-pill--ok"
                      }
                    >
                      {user.status === "pending" ? "Pending" : "Active"}
                    </span>
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <button type="button" className="mv-btn mv-btn--soft">
                      {user.status === "pending" ? "Approve" : "Manage"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </AppShell>
  );
}
