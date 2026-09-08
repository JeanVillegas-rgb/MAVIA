import { Link, NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { ROLE_LABEL, displayName, initialsFor } from "../roles";

/**
 * Shared authenticated layout: pill top bar + dark left sidebar + optional
 * right rail. Each role dashboard passes its own `nav`, `primaryAction`,
 * and `rail`.
 */
export default function AppShell({ nav = [], primaryAction, rail, children }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className="mv-app">
      <div className="mv-topbar">
        <Link to="/" className="mv-brand" style={{ color: "var(--mv-brand-900)" }}>
          <span
            className="mv-brand__dot"
            style={{ background: "var(--mv-brand-500)" }}
            aria-hidden="true"
          />
          MAVIA
        </Link>
        <div className="mv-topbar__nav">
          <Link to="/">Home</Link>
          <Link to="/about">About us</Link>
          <Link to="/contact">Contact</Link>
          <button type="button" className="mv-userbtn" onClick={handleLogout}>
            <span className="mv-avatar" style={{ width: 28, height: 28, fontSize: ".7rem" }}>
              {initialsFor(user)}
            </span>
            Log out
          </button>
        </div>
      </div>

      <div className={rail ? "mv-app__body" : "mv-app__body mv-app__body--no-rail"}>
        <aside className="mv-side">
          {primaryAction ? (
            <Link to={primaryAction.to} className="mv-btn mv-btn--light mv-side__cta">
              {primaryAction.label}
            </Link>
          ) : null}

          <nav className="mv-side__nav">
            {nav.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  isActive ? "mv-side__link is-active" : "mv-side__link"
                }
              >
                <span className="mv-side__icon" aria-hidden="true">
                  {item.icon}
                </span>
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="mv-side__spacer" />
          <div>
            <div className="mv-side__group-label">Signed in as</div>
            <div className="mv-side__link" style={{ cursor: "default" }}>
              <span className="mv-avatar" style={{ width: 28, height: 28, fontSize: ".7rem" }}>
                {initialsFor(user)}
              </span>
              <span style={{ overflow: "hidden" }}>
                <span style={{ display: "block", lineHeight: 1.2 }}>
                  {displayName(user)}
                </span>
                <small style={{ color: "rgba(255,255,255,.55)", fontWeight: 400 }}>
                  {ROLE_LABEL[user?.role] || user?.role}
                </small>
              </span>
            </div>
          </div>
        </aside>

        <main className="mv-main">{children}</main>

        {rail ? <aside className="mv-rail">{rail}</aside> : null}
      </div>
    </div>
  );
}
