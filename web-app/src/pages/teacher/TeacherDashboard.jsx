import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import TeacherShell from "./TeacherShell";
import { useAuth } from "../../auth";
import { displayName } from "../../roles";
import { fetchCourses } from "../../api";

export default function TeacherDashboard() {
  const { user } = useAuth();
  const [courses, setCourses] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    fetchCourses().then(data => { if (active) setCourses(data); })
      .catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, []);
  return <TeacherShell>
    <div className="mv-page-head"><h1>Welcome, {displayName(user)}</h1>
      <p>Prepare lessons, review connections, and publish learning materials.</p></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <section className="mv-card">
      <h2 className="mv-card__title">Prepare a lesson</h2>
      <p>Create a course or open an existing one to upload its outline and lesson PDFs.</p>
      <Link className="mv-btn" to="/courses/new">Create course</Link>
    </section>
    <section className="mv-card">
      <h2 className="mv-card__title">Courses {courses ? `(${courses.length})` : ""}</h2>
      {courses === null && !error && <p role="status">Loading courses...</p>}
      {courses?.length === 0 && <p>No courses yet.</p>}
      {courses?.map(course => <div className="mv-list__item" key={course.id}>
        <Link to={`/courses/${course.id}`}>{course.title}</Link>
        <Link className="mv-btn mv-btn--soft" to={`/review/courses/${course.id}`}>Review content</Link>
      </div>)}
    </section>
  </TeacherShell>;
}
