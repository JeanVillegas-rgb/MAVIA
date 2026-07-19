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
          <h2>Course outline to lesson hierarchy</h2>
          <p>
            Teachers create a course, upload a course outline, and Mavia uses the LLM to extract
            the topic hierarchy automatically.
          </p>
        </div>
        <div className="card">
          <h3>Create a course</h3>
          <CreateCourseForm onCreated={handleCreated} />
        </div>
      </section>

      <section className="card">
        <h3>Your courses</h3>
        <CourseList refreshKey={refreshKey} />
      </section>
    </>
  );
}
