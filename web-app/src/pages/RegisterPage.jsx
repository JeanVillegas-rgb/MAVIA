import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { register } from "../api";

// Web registration is teacher-only — students sign up from the MAVIA mobile
// app, and admins are provisioned rather than self-registered.
export default function RegisterPage() {
  const navigate = useNavigate();
  const [form, setForm] = useState({
    first_name: "",
    last_name: "",
    username: "",
    email: "",
    password: "",
    role: "TEACHER",
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  function update(field) {
    return (event) =>
      setForm((prev) => ({ ...prev, [field]: event.target.value }));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const result = await register(form);
      if (result.verification_required === false) {
        navigate("/login", { replace: true });
      } else {
        setDone(true);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <div className="mv-root">
        <div className="mv-auth">
          <aside className="mv-auth__aside">
            <Link to="/" className="mv-brand">
              <span className="mv-brand__dot" aria-hidden="true" />
              MAVIA
            </Link>
            <div>
              <h2>Almost there.</h2>
              <p>One click in your inbox and you&apos;re in.</p>
            </div>
            <p className="mv-muted" style={{ color: "rgba(255,255,255,.6)" }}>
              Shaped for Touch, Heard to Learn.
            </p>
          </aside>
          <div className="mv-auth__main">
            <div className="mv-auth__card">
              <p className="mv-eyebrow">Check your inbox</p>
              <h1>Verify your email</h1>
              <p>
                We sent a verification link to{" "}
                <strong>{form.email}</strong>. Click it to activate your account,
                then log in.
              </p>
              <p className="mv-auth__foot" style={{ marginTop: "2rem" }}>
                <Link to="/login">Go to log in</Link>
                {"  ·  "}
                <Link to="/verify-email/resend">Resend link</Link>
              </p>
            </div>
          </div>
        </div>
      </div>
    );
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
            <h2>Build lessons for touch and sound.</h2>
            <p>
              Create a teacher account to author courses and review the audio
              lessons MAVIA generates for your students.
            </p>
            <div className="mv-auth__points">
              <span className="mv-auth__point">Upload a course outline, get a topic hierarchy</span>
              <span className="mv-auth__point">Review and approve generated lessons</span>
              <span className="mv-auth__point">Students learn on the MAVIA mobile app</span>
            </div>
          </div>
          <p className="mv-muted" style={{ color: "rgba(255,255,255,.6)" }}>
            Shaped for Touch, Heard to Learn.
          </p>
        </aside>

        <div className="mv-auth__main">
          <div className="mv-auth__card">
            <p className="mv-eyebrow">Teacher sign up</p>
            <h1>Create your account</h1>
            <p>It only takes a minute.</p>

            <form className="mv-form" onSubmit={handleSubmit}>
              <div className="mv-field__row">
                <div className="mv-field">
                  <label htmlFor="reg-first">First name</label>
                  <input
                    id="reg-first"
                    type="text"
                    autoComplete="given-name"
                    value={form.first_name}
                    onChange={update("first_name")}
                  />
                </div>
                <div className="mv-field">
                  <label htmlFor="reg-last">Last name</label>
                  <input
                    id="reg-last"
                    type="text"
                    autoComplete="family-name"
                    value={form.last_name}
                    onChange={update("last_name")}
                  />
                </div>
              </div>

              <div className="mv-field">
                <label htmlFor="reg-username">Username</label>
                <input
                  id="reg-username"
                  type="text"
                  autoComplete="username"
                  value={form.username}
                  onChange={update("username")}
                  required
                />
              </div>
              <div className="mv-field">
                <label htmlFor="reg-email">Email</label>
                <input
                  id="reg-email"
                  type="email"
                  autoComplete="email"
                  value={form.email}
                  onChange={update("email")}
                  required
                />
              </div>
              <div className="mv-field">
                <label htmlFor="reg-password">Password</label>
                <input
                  id="reg-password"
                  type="password"
                  autoComplete="new-password"
                  value={form.password}
                  onChange={update("password")}
                  required
                />
              </div>

              {error && <div className="mv-alert">{error}</div>}

              <button
                className="mv-btn mv-btn--block mv-btn--lg"
                type="submit"
                disabled={submitting}
              >
                {submitting ? "Creating account…" : "Create account"}
              </button>
            </form>

            <p className="mv-auth__foot">
              Already have an account? <Link to="/login">Log in</Link>
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
