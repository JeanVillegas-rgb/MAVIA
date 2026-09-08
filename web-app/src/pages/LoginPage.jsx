import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { homePathForRole } from "../roles";

export default function LoginPage() {
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
      const user = await login(username, password);
      const redirectTo = location.state?.from || homePathForRole(user.role);
      navigate(redirectTo, { replace: true });
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
            <h2>Welcome back.</h2>
            <p>
              Sign in to pick up where your learners left off — lessons, audio
              narratives, and progress all in one place.
            </p>
            <div className="mv-auth__points">
              <span className="mv-auth__point">Students resume adaptive lessons</span>
              <span className="mv-auth__point">Teachers review and approve content</span>
              <span className="mv-auth__point">Admins manage accounts and access</span>
            </div>
          </div>
          <p className="mv-muted" style={{ color: "rgba(255,255,255,.6)" }}>
            Shaped for Touch, Heard to Learn.
          </p>
        </aside>

        <div className="mv-auth__main">
          <div className="mv-auth__card">
            <h1>Log in</h1>
            <p>Enter your credentials to continue.</p>

            <form className="mv-form" onSubmit={handleSubmit}>
              <div className="mv-field">
                <label htmlFor="login-username">Username</label>
                <input
                  id="login-username"
                  type="text"
                  autoComplete="username"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  required
                />
              </div>
              <div className="mv-field">
                <label htmlFor="login-password">Password</label>
                <input
                  id="login-password"
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
              New to MAVIA? <Link to="/register">Create an account</Link>
            </p>
            <p className="mv-auth__foot" style={{ marginTop: "0.4rem" }}>
              Didn&apos;t get the verification email?{" "}
              <Link to="/verify-email/resend">Resend it</Link>
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
