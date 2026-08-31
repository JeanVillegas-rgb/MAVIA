import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { register } from "../api";

const ROLES = ["STUDENT", "TEACHER"];

export default function RegisterPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ username: "", email: "", password: "", role: "STUDENT" });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  function update(field) {
    return (event) => setForm((prev) => ({ ...prev, [field]: event.target.value }));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await register(form);
      await login(form.username, form.password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="hero">
      <div className="card" style={{ maxWidth: 360, margin: "0 auto" }}>
        <h3>Create an account</h3>
        <form className="upload-form" onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="reg-username">Username</label>
            <input id="reg-username" type="text" value={form.username} onChange={update("username")} />
          </div>
          <div className="field">
            <label htmlFor="reg-email">Email</label>
            <input id="reg-email" type="email" value={form.email} onChange={update("email")} />
          </div>
          <div className="field">
            <label htmlFor="reg-password">Password</label>
            <input
              id="reg-password"
              type="password"
              autoComplete="new-password"
              value={form.password}
              onChange={update("password")}
            />
          </div>
          <div className="field">
            <label htmlFor="reg-role">Role</label>
            <select id="reg-role" value={form.role} onChange={update("role")}>
              {ROLES.map((role) => (
                <option key={role} value={role}>
                  {role.charAt(0) + role.slice(1).toLowerCase()}
                </option>
              ))}
            </select>
          </div>
          {error && <div className="error-banner">{error}</div>}
          <button className="btn btn-primary" type="submit" disabled={submitting}>
            {submitting ? "Creating account..." : "Create account"}
          </button>
        </form>
        <p style={{ marginTop: "0.8rem" }}>
          Already have an account? <Link to="/login">Log in</Link>
        </p>
      </div>
    </section>
  );
}
