import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../../auth";

// A second way in, for guardians. It posts to the same /auth/login endpoint as
// the main login screen, because there is no GUARDIAN role on the account model
// yet; what differs is the entry point, the copy and where a successful login
// lands (the guardian view rather than the role's own home). Once guardian
// accounts exist, this page keeps working unchanged: only the redirect below
// needs to become role-aware.
export default function GuardianLoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await login(username, password);
      navigate(location.state?.from || "/guardian", { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mv-root">
      <div className="mv-auth">
        <aside className="mv-auth__aside">
          <Link to="/" className="mv-brand">
            <span className="mv-brand__dot" aria-hidden="true" />
            MAVIA
          </Link>
          <div>
            <h2>Follow your child&rsquo;s learning.</h2>
            <p>
              Sign in to see which lessons your child has finished, how they
              scored, and which topics they are still working on.
            </p>
            <div className="mv-auth__points">
              <span className="mv-auth__point">Progress for every topic</span>
              <span className="mv-auth__point">Scores lesson by lesson</span>
              <span className="mv-auth__point">No lessons are changed from here</span>
            </div>
          </div>
          <p className="mv-muted" style={{ color: "rgba(255,255,255,.6)" }}>
            Shaped for Touch, Heard to Learn.
          </p>
        </aside>

        <div className="mv-auth__main">
          <div className="mv-auth__card">
            <h1>Guardian log in</h1>
            <p>Use the account that was linked to your child.</p>

            <form className="mv-form" onSubmit={handleSubmit}>
              <div className="mv-field">
                <label htmlFor="guardian-username">Username</label>
                <input
                  id="guardian-username"
                  type="text"
                  autoComplete="username"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  required
                />
              </div>
              <div className="mv-field">
                <label htmlFor="guardian-password">Password</label>
                <input
                  id="guardian-password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                />
              </div>

              {error && <div className="mv-alert">{error}</div>}

              <button
                className="mv-btn mv-btn--block mv-btn--lg"
                type="submit"
                disabled={submitting}
              >
                {submitting ? "Logging in…" : "Log in"}
              </button>
            </form>

            <p className="mv-auth__foot">
              Are you a teacher or an administrator?{" "}
              <Link to="/login">Log in here</Link>
            </p>
            <p className="mv-auth__foot" style={{ marginTop: "0.4rem" }}>
              A guardian account is created with your child&rsquo;s. Ask your
              child&rsquo;s teacher if you have not received yours.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
