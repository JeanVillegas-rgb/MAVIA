import { useState } from "react";
import { createCourse } from "../api";

export default function CreateCourseForm({ onCreated }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();
    if (!title.trim()) {
      setError("Course title is required.");
      return;
    }

    setSubmitting(true);
    setError("");
    try {
      const course = await createCourse({
        title: title.trim(),
        description: description.trim(),
      });
      setTitle("");
      setDescription("");
      onCreated(course);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="upload-form" onSubmit={handleSubmit}>
      <div className="field">
        <label htmlFor="course-title">Course group name</label>
        <input
          id="course-title"
          type="text"
          placeholder="Grade 8 Science — Fall 2026"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="course-description">Description (optional)</label>
        <input
          id="course-description"
          type="text"
          placeholder="Life science unit for section B"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </div>
      {error && <div className="error-banner">{error}</div>}
      <button className="btn btn-primary" type="submit" disabled={submitting}>
        {submitting ? "Creating..." : "Create course group"}
      </button>
    </form>
  );
}
