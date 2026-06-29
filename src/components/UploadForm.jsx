import { useState } from "react";
import { uploadLesson } from "../api";

export default function UploadForm({ onUploaded }) {
  const [title, setTitle] = useState("");
  const [file, setFile] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();
    if (!title.trim() || !file) {
      setError("Please provide a lesson title and PDF file.");
      return;
    }

    setSubmitting(true);
    setError("");

    try {
      const formData = new FormData();
      formData.append("title", title.trim());
      formData.append("pdf_file", file);
      const lesson = await uploadLesson(formData);
      setTitle("");
      setFile(null);
      event.target.reset();
      onUploaded(lesson);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="upload-form" onSubmit={handleSubmit}>
      <div className="field">
        <label htmlFor="title">Lesson title</label>
        <input
          id="title"
          type="text"
          placeholder="Photosynthesis Adventure"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="pdf">Science lesson PDF</label>
        <input
          id="pdf"
          type="file"
          accept="application/pdf,.pdf"
          onChange={(event) => setFile(event.target.files?.[0] || null)}
        />
      </div>
      {error && <div className="error-banner">{error}</div>}
      <button className="btn btn-primary" type="submit" disabled={submitting}>
        {submitting ? "Uploading..." : "Create story audiobook"}
      </button>
    </form>
  );
}
