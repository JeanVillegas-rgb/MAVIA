import { Link } from "react-router-dom";
import AppShell from "../../components/AppShell";

export default function AdminDashboard() {
  return <AppShell nav={[
    { to: "/admin", label: "Overview", end: true },
    { to: "/courses", label: "Courses" },
    { to: "/review", label: "Review" },
  ]}>
    <div className="mv-page-head"><h1>Admin overview</h1>
      <p>Manage and review course content.</p></div>
    <section className="mv-card"><h2 className="mv-card__title">Course management</h2>
      <Link to="/courses" className="mv-btn">Open courses</Link></section>
    <section className="mv-card"><h2 className="mv-card__title">Adaptive configuration</h2>
      <p>Weights apply to the new enrollment-based engine. Existing legacy adaptive progress is kept separately.</p><Link to="/admin/adaptive-weights" className="mv-btn">Edit weights</Link></section>
  </AppShell>;
}
