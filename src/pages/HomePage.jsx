import { useState } from "react";
import { useNavigate } from "react-router-dom";
import CourseList from "../components/CourseList";
import CreateCourseForm from "../components/CreateCourseForm";

export default function HomePage() {
  const navigate = useNavigate();
  const [refreshKey, setRefreshKey] = useState(0);

  function handleCreated(course) {
    setRefreshKey((value) => value + 1);
    navigate(`/courses/${course.id}`);
  }

  return (
    <>
      <section className="hero">
        <div className="hero-copy card">
          <h2>Course-first science audiobooks</h2>
          <p>
            Professors create a course group, upload a course outline to build a lesson hierarchy,
            then attach PDFs to each node. Review and edit narration scripts before audio is
            generated, then approve lessons for students to consume.
          </p>
        </div>
        <div className="card">
          <h3>Step 1 — Create a course group</h3>
          <CreateCourseForm onCreated={handleCreated} />
        </div>
      </section>

      <section className="card">
        <h3>Your course groups</h3>
        <CourseList refreshKey={refreshKey} />
      </section>
    </>
  );
}
