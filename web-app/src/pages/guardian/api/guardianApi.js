// Every request the guardian view will make, in one place.
//
// The guardian role is planned work: there is no GUARDIAN role on the account
// model and no endpoint behind these screens yet. So each function here is a
// seam, not an implementation -- it returns the shape the screens already
// read, from api/placeholderData.js, and names the endpoint that will replace
// it. Swapping one in is a one-line change inside that function, and no screen
// has to change with it.
//
// Same pattern as the mobile app's src/data/library.ts, for the same reason.
//
// Nothing here is measured. Do not quote these figures anywhere.

import {
  child,
  recent,
  scores,
  summary,
  topics,
  engineNote,
  SCORE_FOOTNOTE,
} from "./placeholderData";

// The teacher-facing equivalent already exists and is real:
// backend/adaptive/views.py::CourseProgressView, at
// GET /api/adaptive/courses/<course_id>/progress/. It returns per-student
// mastery, attempts, questions answered, correct rate and modules completed
// for a whole class. A guardian endpoint is that same query narrowed to the
// one learner a guardian is linked to, which is why the shapes below line up
// with it rather than inventing new fields.

/** Resolve after a beat, so a screen written against this sees the loading
 *  state it will see against a real network. */
function placeholder(value) {
  return new Promise((resolve) => {
    setTimeout(() => resolve(value), 150);
  });
}

/** The learner this guardian is looking at, plus any siblings to switch to.
 *  TODO: GET /api/guardian/children/ -> pick the selected child. */
export function fetchChild() {
  return placeholder(child);
}

/** The four headline figures on the overview.
 *  TODO: GET /api/guardian/children/<id>/summary/
 *  Mastery comes from LearningState.mastery (BKT); lessons done and questions
 *  answered are counted from StudentResponse. */
export function fetchSummary() {
  return placeholder(summary);
}

/** Per-topic mastery, for the progress bars.
 *  TODO: GET /api/guardian/children/<id>/topics/
 *  One row per published topic, from LearningState.concept_mastery. */
export function fetchTopics() {
  return placeholder(topics);
}

/** The most recent lessons, newest first.
 *  TODO: GET /api/guardian/children/<id>/recent/?limit=4 */
export function fetchRecent() {
  return placeholder(recent);
}

/** Every answered lesson, newest first.
 *  TODO: GET /api/guardian/children/<id>/scores/ */
export function fetchScores() {
  return placeholder(scores);
}

/** The engine's plain-sentence note about what it is doing for this learner.
 *  TODO: derive from the decision log -- StudentResponse.action tells you
 *  whether the learner was escalated, detoured or moved on, and
 *  adaptive/services.py is where those actions are set. */
export function fetchEngineNote() {
  return placeholder(engineNote);
}

/** Static explanatory copy. Stays local; there is no endpoint for prose. */
export { SCORE_FOOTNOTE };
