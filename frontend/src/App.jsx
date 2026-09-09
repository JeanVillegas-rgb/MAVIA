import { Navigate, Route, Routes } from "react-router-dom";

import LandingPage from "./pages/LandingPage";
import MarketingPage from "./pages/MarketingPage";
import LoginPage from "./pages/LoginPage";
import RegisterPage from "./pages/RegisterPage";
import VerifyEmailPage from "./pages/VerifyEmailPage";

import GetMobileAppPage from "./pages/GetMobileAppPage";
import TeacherDashboard from "./pages/teacher/TeacherDashboard";
import CoursesPage from "./pages/teacher/CoursesPage";
import NewCoursePage from "./pages/teacher/NewCoursePage";
import TeacherShell from "./pages/teacher/TeacherShell";
import CourseDetailPage from "./pages/CourseDetailPage";
import TopicDetailPage from "./pages/TopicDetailPage";
import ReviewCoursesPage from "./pages/teacher/ReviewCoursesPage";
import CourseReviewPage from "./pages/teacher/CourseReviewPage";
import AdminDashboard from "./pages/admin/AdminDashboard";
import AdaptiveWeightsPage from "./pages/admin/AdaptiveWeightsPage";

import LearningPathPage from "./pages/LearningPathPage";
import { RequireAuth, RequireRole } from "./components/RouteGuards";
import { ROLES, homePathForRole } from "./roles";
import { useAuth } from "./auth";

// Sends an already-authenticated visitor away from /login and /register.
function GuestOnly({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="mv-loading">Loading…</div>;
  if (user) return <Navigate to={homePathForRole(user.role)} replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      {/* Public */}
      <Route path="/" element={<LandingPage />} />
      <Route
        path="/about"
        element={
          <MarketingPage title="About MAVIA">
            MAVIA is a learning platform for young blind and visually impaired
            students, pairing a physical 6-button braille interface with
            responsive audio lessons.
          </MarketingPage>
        }
      />
      <Route
        path="/contact"
        element={
          <MarketingPage title="Contact us">
            Reach the MAVIA team at hello@mavia.example — we would love to hear
            from schools and educators.
          </MarketingPage>
        }
      />

      {/* Auth */}
      <Route
        path="/login"
        element={
          <GuestOnly>
            <LoginPage />
          </GuestOnly>
        }
      />
      <Route
        path="/register"
        element={
          <GuestOnly>
            <RegisterPage />
          </GuestOnly>
        }
      />
      <Route path="/verify-email" element={<VerifyEmailPage />} />
      <Route path="/verify-email/:token" element={<VerifyEmailPage />} />

      {/* Role dashboards */}
      <Route
        path="/student"
        element={
          <RequireRole allow={ROLES.STUDENT}>
            <GetMobileAppPage />
          </RequireRole>
        }
      />
      <Route
        path="/teacher"
        element={
          <RequireRole allow={ROLES.TEACHER}>
            <TeacherDashboard />
          </RequireRole>
        }
      />

      {/* Content-generation pipeline (teacher + admin) — ported from Jure branch */}
      <Route
        path="/courses"
        element={
          <RequireRole allow={[ROLES.TEACHER, ROLES.ADMIN]}>
            <CoursesPage />
          </RequireRole>
        }
      />
      <Route
        path="/courses/new"
        element={
          <RequireRole allow={[ROLES.TEACHER, ROLES.ADMIN]}>
            <NewCoursePage />
          </RequireRole>
        }
      />
      <Route
        path="/courses/:id"
        element={
          <RequireRole allow={[ROLES.TEACHER, ROLES.ADMIN]}>
            <TeacherShell>
              <CourseDetailPage />
            </TeacherShell>
          </RequireRole>
        }
      />
      <Route
        path="/courses/:courseId/topics/:topicId"
        element={
          <RequireRole allow={[ROLES.TEACHER, ROLES.ADMIN]}>
            <div className="app-shell">
              <TopicDetailPage />
            </div>
          </RequireRole>
        }
      />
      <Route
        path="/courses/:courseId/topics/:topicId/path"
        element={
          <RequireRole allow={[ROLES.TEACHER, ROLES.ADMIN]}>
            <div className="app-shell">
              <LearningPathPage />
            </div>
          </RequireRole>
        }
      />
      {/* Course review (teacher + admin): inspect the packaged lesson content
          and the progress of each enrolled student. The audiobook-style
          player itself is mobile-only. */}
      <Route
        path="/review"
        element={
          <RequireRole allow={[ROLES.TEACHER, ROLES.ADMIN]}>
            <ReviewCoursesPage />
          </RequireRole>
        }
      />
      <Route
        path="/review/courses/:courseId"
        element={
          <RequireRole allow={[ROLES.TEACHER, ROLES.ADMIN]}>
            <CourseReviewPage />
          </RequireRole>
        }
      />
      <Route
        path="/admin"
        element={
          <RequireRole allow={ROLES.ADMIN}>
            <AdminDashboard />
          </RequireRole>
        }
      />
      <Route
        path="/admin/adaptive-weights"
        element={
          <RequireRole allow={ROLES.ADMIN}>
            <AdaptiveWeightsPage />
          </RequireRole>
        }
      />

      {/* Fallback */}
      <Route
        path="*"
        element={
          <RequireAuth>
            <Navigate to="/" replace />
          </RequireAuth>
        }
      />
    </Routes>
  );
}
