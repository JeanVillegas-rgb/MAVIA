import { Link } from "react-router-dom";
import TeacherShell from "./TeacherShell";
import CourseList from "../../components/CourseList";

// "Courses" tab: every course, each card opening its outline + PDF-upload
// screen.
export default function CoursesPage() {
  return (
    <TeacherShell>
      <div className="mv-page-head">
        <h1>Courses</h1>
        <p>
          Create a course, upload its outline PDF, then map lesson PDFs to the
          extracted topics.
        </p>
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <Link to="/courses/new" className="mv-btn">
          ＋ Add course
        </Link>
      </div>

      <section className="mv-card">
        <CourseList />
      </section>
    </TeacherShell>
  );
}
