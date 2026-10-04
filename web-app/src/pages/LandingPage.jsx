import { Link } from "react-router-dom";
import PublicNav from "../components/PublicNav";
import { useAuth } from "../auth";
import { homePathForRole } from "../roles";

const FEATURES = [
  {
    title: "Physical 6-button braille interface",
    body: "A tactile controller pairs with the app so learners navigate lessons by touch, not by sight.",
  },
  {
    title: "Responsive audio narratives",
    body: "Every lesson is delivered as adaptive audio that responds to how each student answers.",
  },
  {
    title: "Learning paths that shape themselves",
    body: "The platform tracks progress and reshapes the sequence of topics for every child.",
  },
];

export default function LandingPage() {
  const { user } = useAuth();

  return (
    <div className="mv-root">
      <PublicNav />

      <div className="mv-landing">
        <section className="mv-hero">
          <div className="mv-hero__grid">
            <div>
              <p className="mv-eyebrow">Shaped for touch</p>
              <h1>
                Shaped for Touch,
                <br />
                Heard to Learn.
              </h1>
              <p className="mv-hero__lede">
                MAVIA introduces a brand new way for young blind and visually
                impaired students to explore elementary science independently —
                blending a physical braille interface with responsive audio
                narratives.
              </p>
              <div className="mv-hero__actions">
                <Link
                  to={user ? homePathForRole(user.role) : "/register"}
                  className="mv-btn mv-btn--lg"
                >
                  {user ? "Go to dashboard" : "Teacher sign up"}
                </Link>
                <Link to="/about" className="mv-btn mv-btn--ghost mv-btn--lg">
                  Learn more
                </Link>
              </div>
              {!user && (
                <p className="mv-muted" style={{ marginTop: "1rem", fontSize: ".9rem" }}>
                  Students: MAVIA lives on your phone — get the mobile app.
                </p>
              )}
            </div>

            <div className="mv-hero__art">
              <div className="mv-hero__badge">
                <span>
                  <span className="dots">⠍ ⠁ ⠧</span>
                  <span>Heard to Learn</span>
                </span>
              </div>
              <div className="mv-bubble mv-bubble--a">⠿ ⠿</div>
              <div className="mv-bubble mv-bubble--b">⠷ ⠮ ⠽</div>
            </div>
          </div>
        </section>

        <section className="mv-feature-row">
          {FEATURES.map((feature) => (
            <article key={feature.title} className="mv-feature">
              <h3>{feature.title}</h3>
              <p>{feature.body}</p>
            </article>
          ))}
        </section>
      </div>
    </div>
  );
}
