import AppShell from "../../components/AppShell";

// Shared MAVIA chrome (blue sidebar + topbar) for the teacher content-gen
// pages. The pages themselves still use the legacy .card / .field / .btn
// markup, now re-skinned to the blue palette by styles/pipeline.css.
const TEACHER_NAV = [
  { to: "/teacher", label: "Dashboard", icon: "▤", end: true },
  { to: "/courses", label: "Courses", icon: "▦" },
  { to: "/review", label: "Review", icon: "♪" },
  { to: "/teacher/resources", label: "Resources", icon: "❐" },
  { to: "/teacher/settings", label: "Settings", icon: "⚙" },
  // The guardian view is not offered here. It has no GUARDIAN role and no
  // endpoints of its own yet, and a teacher following the link lands on
  // placeholder figures that look like a real learner's. Its screens live on
  // under pages/guardian/, reachable only by typing the address, and its
  // requests are stubbed in pages/guardian/api/guardianApi.js ready for the
  // day the backend lands.
];

export default function TeacherShell({ children }) {
  return (
    <AppShell
      nav={TEACHER_NAV}
      primaryAction={{ to: "/courses/new", label: "＋ Add Course" }}
    >
      {children}
    </AppShell>
  );
}
