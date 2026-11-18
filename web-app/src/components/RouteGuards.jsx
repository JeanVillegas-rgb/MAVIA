import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../auth";
import { homePathForRole } from "../roles";

function FullPageLoading() {
  return <div className="mv-loading">Loading your workspace…</div>;
}

// Requires a signed-in user; bounces guests to a login screen remembering where
// they were. `loginPath` lets a section send guests to its own entry point --
// the guardian view has a second login screen of its own.
export function RequireAuth({ loginPath = "/login", children }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return <FullPageLoading />;
  if (!user) {
    return <Navigate to={loginPath} replace state={{ from: location.pathname }} />;
  }
  return children;
}

// Requires the signed-in user to hold one of `allow`; otherwise sends them to
// their own role home so nobody lands on a blank "forbidden" screen.
export function RequireRole({ allow, children }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  const allowed = Array.isArray(allow) ? allow : [allow];

  if (loading) return <FullPageLoading />;
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (!allowed.includes(user.role)) {
    return <Navigate to={homePathForRole(user.role)} replace />;
  }
  return children;
}
