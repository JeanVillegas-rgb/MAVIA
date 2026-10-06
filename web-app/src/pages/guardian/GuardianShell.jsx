import AppShell from "../../components/AppShell";

// Chrome for the guardian view. Same sidebar and top bar as every other role,
// so the screens read as part of MAVIA rather than as a mock-up. There is no
// GUARDIAN role on the account model yet, so these pages are reachable by any
// signed-in user; see api/guardianApi.js.
const GUARDIAN_NAV = [
  { to: "/guardian", label: "Overview", icon: "▤", end: true },
  { to: "/guardian/scores", label: "Scores", icon: "▥" },
];

export default function GuardianShell({ rail, children }) {
  return (
    <AppShell nav={GUARDIAN_NAV} rail={rail}>
      {children}
    </AppShell>
  );
}
