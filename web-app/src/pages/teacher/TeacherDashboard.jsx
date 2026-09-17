import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import AppShell from "../../components/AppShell";
import { useAuth } from "../../auth";
import { displayName, initialsFor } from "../../roles";
import { fetchCourses } from "../../api";

const NAV = [
  { to: "/teacher", label: "Dashboard", icon: "▤", end: true },
  { to: "/courses", label: "Courses", icon: "▦" },
  { to: "/review", label: "Review", icon: "♪" },
  { to: "/teacher/resources", label: "Resources", icon: "❐" },
  { to: "/teacher/settings", label: "Settings", icon: "⚙" },
];

function courseStatus(course) {
  if (course.outline_approved) return `${course.node_count} topics`;
  if (course.has_outline) return "Outline pending review";
  return "No outline yet";
}

function CourseCarousel({ courses }) {
  const trackRef = useRef(null);
  const [atStart, setAtStart] = useState(true);
  const [atEnd, setAtEnd] = useState(true);

  const sync = useCallback(() => {
    const el = trackRef.current;
    if (!el) return;
    setAtStart(el.scrollLeft <= 1);
    setAtEnd(el.scrollLeft + el.clientWidth >= el.scrollWidth - 1);
  }, []);

  useEffect(() => {
    sync();
    const el = trackRef.current;
    if (!el) return undefined;
    el.addEventListener("scroll", sync, { passive: true });
    window.addEventListener("resize", sync);
    return () => {
      el.removeEventListener("scroll", sync);
      window.removeEventListener("resize", sync);
    };
  }, [sync, courses.length]);

  function page(dir) {
    const el = trackRef.current;
    if (el) el.scrollBy({ left: dir * (el.clientWidth * 0.85), behavior: "smooth" });
  }

  const hasArrows = !(atStart && atEnd);

  return (
    <div className="dashboard-carousel">
      <div className="dashboard-carousel__head">
        <h3 className="mv-card__title" style={{ margin: 0 }}>
          Your courses
        </h3>
        {hasArrows && (
          <div className="dashboard-carousel__arrows">
            <button
              type="button"
              className="dashboard-carousel__arrow"
              aria-label="Previous courses"
              disabled={atStart}
              onClick={() => page(-1)}
            >
              ‹
            </button>
            <button
              type="button"
              className="dashboard-carousel__arrow"
              aria-label="Next courses"
              disabled={atEnd}
              onClick={() => page(1)}
            >
              ›
            </button>
          </div>
        )}
      </div>

      <div className="dashboard-carousel__track" ref={trackRef}>
        {courses.map((course) => (
          <Link
            key={course.id}
            to={`/courses/${course.id}`}
            className="mv-card dashboard-course-card"
          >
            <span className="dashboard-course-card__body">
              <strong>{course.title}</strong>
              <span className="mv-muted">{courseStatus(course)}</span>
            </span>
            <span className="dashboard-course-card__count" title="Students enrolled">
              <span className="dashboard-course-card__count-num">
                {course.enrolled_count || 0}
              </span>
              <span className="dashboard-course-card__count-label">
                student{course.enrolled_count === 1 ? "" : "s"}
              </span>
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}

export default function TeacherDashboard() {
  const { user } = useAuth();
  const [courses, setCourses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetchCourses()
      .then((data) => {
        if (!cancelled) setCourses(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const totalStudents = courses.reduce(
    (sum, course) => sum + (course.enrolled_count || 0),
    0
  );

  const rail = (
    <>
      <div className="mv-rail__card">
        <div className="mv-profile">
          <span className="mv-avatar">{initialsFor(user)}</span>
          <span>
            <span className="mv-profile__name">{displayName(user)}</span>
            <span className="mv-profile__role">Teacher</span>
          </span>
        </div>
        <p className="mv-rail__label">At a glance</p>
        <span className="mv-chip">{courses.length} courses</span>
        <span className="mv-chip">{totalStudents} students enrolled</span>
      </div>

      <div className="mv-rail__card">
        <p className="mv-rail__label">Quick links</p>
        <Link to="/courses/new" className="mv-chip">＋ New course</Link>
        <Link to="/review" className="mv-chip">Review progress</Link>
      </div>
    </>
  );

  return (
    <AppShell
      nav={NAV}
      rail={rail}
      primaryAction={{ to: "/courses/new", label: "＋ Add Course" }}
    >
      <div className="mv-page-head">
        <h1>What should we explore today?</h1>
        <p>Your courses at a glance, with how many students are enrolled in each.</p>
      </div>

      {loading && <div className="empty-state">Loading courses…</div>}
      {error && <div className="error-banner">{error}</div>}

      {!loading && !error && courses.length === 0 && (
        <div className="empty-state">
          No courses yet.{" "}
          <Link to="/courses/new">Create one</Link>, then upload its outline.
        </div>
      )}

      {!loading && !error && courses.length > 0 && (
        <CourseCarousel courses={courses} />
      )}

      <div className="dashboard-bottom-grid">
        <section className="mv-card">
          <h3 className="mv-card__title">To verify</h3>
          <p className="mv-muted" style={{ fontSize: ".9rem", marginBottom: ".9rem" }}>
            Check generated questions and lesson audio, and see how enrolled
            students are progressing.
          </p>
          <Link to="/review" className="mv-btn mv-btn--soft">
            ▶ Open Review
          </Link>
        </section>

        <section className="mv-card">
          <h3 className="mv-card__title">Courses</h3>
          <p className="mv-muted" style={{ fontSize: ".9rem", marginBottom: ".9rem" }}>
            Create a course, upload its outline PDF, then map lesson PDFs to the
            extracted topics.
          </p>
          <Link to="/courses" className="mv-btn mv-btn--soft">
            Open Courses →
          </Link>
        </section>
      </div>

      <p className="mv-muted" style={{ fontSize: ".8rem", marginTop: "1rem" }}>
        Shaped for Touch, Heard to Learn.
      </p>
    </AppShell>
  );
}
