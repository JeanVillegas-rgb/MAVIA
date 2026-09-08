import { Link, NavLink } from "react-router-dom";
import { useAuth } from "../auth";
import { homePathForRole } from "../roles";

// Floating pill navigation used on the landing + marketing routes.
export default function PublicNav() {
  const { user } = useAuth();

  return (
    <div className="mv-nav-wrap">
      <nav className="mv-nav">
        <Link to="/" className="mv-brand">
          <span className="mv-brand__dot" aria-hidden="true" />
          MAVIA
        </Link>

        <div className="mv-nav__links">
          {[
            { to: "/", label: "Home", end: true },
            { to: "/about", label: "About us" },
            { to: "/contact", label: "Contact" },
          ].map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className={({ isActive }) => (isActive ? "is-active" : undefined)}
            >
              {link.label}
            </NavLink>
          ))}
        </div>

        <div className="mv-nav__cta">
          {user ? (
            <Link
              to={homePathForRole(user.role)}
              className="mv-btn mv-btn--light"
            >
              Go to dashboard
            </Link>
          ) : (
            <>
              <Link to="/login" className="mv-btn mv-btn--ghost">
                Log in
              </Link>
              <Link to="/register" className="mv-btn mv-btn--light">
                Sign up
              </Link>
            </>
          )}
        </div>
      </nav>
    </div>
  );
}
