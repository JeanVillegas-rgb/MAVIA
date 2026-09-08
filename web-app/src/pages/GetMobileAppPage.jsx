import AppShell from "../components/AppShell";
import { useAuth } from "../auth";
import { displayName } from "../roles";

// Students authenticate fine on the web (same API), but the student
// experience itself lives in the MAVIA mobile app — this stands in for the
// dashboard so a student who signs in here isn't met with a dead end.
export default function GetMobileAppPage() {
  const { user } = useAuth();
  const firstName = displayName(user).split(" ")[0] || "there";

  return (
    <AppShell nav={[]}>
      <div className="mv-page-head">
        <h1>Hi {firstName} 👋</h1>
        <p>MAVIA for students lives on your phone.</p>
      </div>

      <section className="mv-card mv-card--tint">
        <div className="mv-prompt">
          <h2>Get the MAVIA app</h2>
          <p className="mv-muted" style={{ marginTop: ".5rem", maxWidth: "42ch", marginInline: "auto" }}>
            Lessons, audio narratives, and your progress are all on the MAVIA
            mobile app. Install it and log in with the same username and
            password you just used here.
          </p>
        </div>
      </section>

      <section className="mv-card">
        <h3 className="mv-card__title">Why mobile?</h3>
        <p className="mv-muted" style={{ fontSize: ".92rem", lineHeight: 1.6 }}>
          MAVIA pairs with a physical 6-button braille interface and
          responsive audio lessons — built for touch, not a mouse and
          keyboard. The web app stays focused on the teacher and admin tools
          behind the scenes.
        </p>
      </section>
    </AppShell>
  );
}
