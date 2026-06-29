import { useState } from "react";

export default function UploadPanel({ onUpload, uploading }) {
  const [file, setFile] = useState(null);
  const [title, setTitle] = useState("");
  const [dragOver, setDragOver] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!file || uploading) return;
    await onUpload(file, title);
    setFile(null);
    setTitle("");
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped?.type === "application/pdf") setFile(dropped);
  };

  return (
    <section className="panel upload-panel">
      <h2>Upload a science lesson</h2>
      <p className="muted">
        Drop a PDF and Mavia will read the text, interpret diagrams, and turn
        each chapter into a storylike audiobook module.
      </p>

      <form onSubmit={handleSubmit}>
        <div
          className={`dropzone ${dragOver ? "drag-over" : ""} ${file ? "has-file" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
        >
          {file ? (
            <div className="file-chip">
              <span className="file-icon">📄</span>
              <span>{file.name}</span>
              <button type="button" className="link-btn" onClick={() => setFile(null)}>
                Remove
              </button>
            </div>
          ) : (
            <>
              <span className="drop-icon">↑</span>
              <p>Drag & drop your PDF here</p>
              <label className="browse-btn">
                Browse files
                <input
                  type="file"
                  accept="application/pdf"
                  hidden
                  onChange={(e) => setFile(e.target.files?.[0] || null)}
                />
              </label>
            </>
          )}
        </div>

        <label className="field">
          <span>Lesson title (optional)</span>
          <input
            type="text"
            placeholder="e.g. The Water Cycle"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>

        <button type="submit" className="primary-btn" disabled={!file || uploading}>
          {uploading ? "Uploading…" : "Create audiobook modules"}
        </button>
      </form>
    </section>
  );
}
