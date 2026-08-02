import { Link, Route, Routes } from "react-router-dom";
import HomePage from "./pages/HomePage";
import CourseDetailPage from "./pages/CourseDetailPage";
import TopicDetailPage from "./pages/TopicDetailPage";

export default function App() {
  return (
    <div className="app-shell">
      <header className="topbar">
        <Link to="/" className="brand">
          <h1>Mavia</h1>
          <small>LLM-powered course outline hierarchy</small>
        </Link>
      </header>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/courses/:id" element={<CourseDetailPage />} />
        <Route path="/courses/:courseId/topics/:topicId" element={<TopicDetailPage />} />
      </Routes>
    </div>
  );
}
