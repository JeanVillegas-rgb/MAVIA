import { Link, Route, Routes } from "react-router-dom";
import HomePage from "./pages/HomePage";
import CourseDetailPage from "./pages/CourseDetailPage";
import TopicDetailPage from "./pages/TopicDetailPage";
import LoginPage from "./pages/LoginPage";
import RegisterPage from "./pages/RegisterPage";
import { useAuth } from "./auth";

function AuthStatus() {
  const { user, loading, logout } = useAuth();

  if (loading) {
    return null;
  }

  if (!user) {
    return (
      <div className="auth-status">
        <Link to="/login" className="btn btn-secondary btn-small">
          Log in
        </Link>
        <Link to="/register" className="btn btn-secondary btn-small">
          Sign up
        </Link>
      </div>
    );
  }

  return (
    <div className="auth-status">
      <span>
        {user.username} <small>({user.role})</small>
      </span>
      <button className="btn btn-secondary btn-small" onClick={logout}>
        Log out
      </button>
    </div>
  );
}

export default function App() {
  return (
    <div className="app-shell">
      <header className="topbar">
        <Link to="/" className="brand">
          <h1>Mavia</h1>
          <small>LLM-powered course outline hierarchy</small>
        </Link>
        <AuthStatus />
      </header>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/courses/:id" element={<CourseDetailPage />} />
        <Route path="/courses/:courseId/topics/:topicId" element={<TopicDetailPage />} />
      </Routes>
    </div>
  );
}
