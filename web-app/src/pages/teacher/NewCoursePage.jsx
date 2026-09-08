import { useNavigate } from "react-router-dom";
import TeacherShell from "./TeacherShell";
import CreateCourseForm from "../../components/CreateCourseForm";

// "Add course" → name it, then land straight on its outline screen.
export default function NewCoursePage() {
  const navigate = useNavigate();

  return (
    <TeacherShell>
      <div className="mv-page-head">
        <h1>New course</h1>
        <p>Step 1 — name it, then upload the outline PDF on the next screen.</p>
      </div>

      <section className="mv-card mv-card--tint">
        <p className="mv-muted" style={{ maxWidth: "52ch" }}>
          MAVIA extracts the topic hierarchy from the outline for your review,
          then lesson PDFs get mapped to the right topics automatically.
        </p>
      </section>

      <section className="mv-card">
        <h3 className="mv-card__title">Create a course</h3>
        <CreateCourseForm
          onCreated={(course) => navigate(`/courses/${course.id}`)}
        />
      </section>
    </TeacherShell>
  );
}
