// Placeholder data for the guardian view.
//
// The guardian role is planned work: there is no GUARDIAN role on the account
// model and no endpoint behind these screens yet, so everything here is
// hardcoded. When the backend lands, replace these exports with API calls and
// the pages should need no other change -- they already read this shape:
//
//   child      -> the learner a guardian is looking at
//   summary    -> the four headline figures
//   topics     -> per-topic mastery, for the progress bars
//   recent     -> the most recent lessons, newest first
//   scores     -> every answered lesson, newest first
//
// Figures are illustrative, not measured.

export const child = {
  name: "Jean Villegas",
  course: "Science",
  grade: "Grade 5",
  teacher: "Ms. Kalina Reyes",
  siblings: ["Jean Villegas", "Noel Villegas"],
};

export const summary = [
  { value: "78%", label: "Mastery", note: "Up 12% this week" },
  { value: "6 of 10", label: "Lessons done", note: "2 lessons this week" },
  { value: "84", label: "Questions answered", note: "66 answered correctly" },
  { value: "Today", label: "Last active", note: "9:12 AM, 24 minutes" },
];

export const topics = [
  { title: "Solid, Liquid and Gas", mastery: 92, state: "finished" },
  { title: "Reproduction Among Flowering Plants", mastery: 74, state: "in progress" },
  { title: "Human Major Body Organs", mastery: 41, state: "needs practice" },
  { title: "Grouping Materials Based on Properties", mastery: 0, state: "not started" },
];

export const engineNote =
  "Jean heard Human Major Body Organs three times and still missed its questions. MAVIA is now teaching it with a simpler explanation.";

export const recent = [
  { lesson: "Pollination", when: "Today, 9:12 AM", score: "4 of 4 correct", ok: true },
  { lesson: "Parts of a Flower", when: "Yesterday, 4:03 PM", score: "3 of 4 correct", ok: true },
  { lesson: "The Digestive System", when: "Mon, 7:45 PM", score: "1 of 4 correct", ok: false },
  { lesson: "States of Matter", when: "Sun, 10:20 AM", score: "4 of 4 correct", ok: true },
];

export const scores = [
  { lesson: "Pollination", topic: "Flowering Plants", date: "23 Sep", score: "4 of 4", result: "Mastered" },
  { lesson: "Parts of a Flower", topic: "Flowering Plants", date: "22 Sep", score: "3 of 4", result: "Mastered" },
  { lesson: "The Digestive System", topic: "Body Organs", date: "21 Sep", score: "1 of 4", result: "Needs practice" },
  { lesson: "States of Matter", topic: "Solid, Liquid and Gas", date: "20 Sep", score: "4 of 4", result: "Mastered" },
  { lesson: "How Matter Changes", topic: "Solid, Liquid and Gas", date: "18 Sep", score: "3 of 4", result: "Mastered" },
  { lesson: "Seed Formation", topic: "Flowering Plants", date: "17 Sep", score: "2 of 4", result: "Learning" },
  { lesson: "Everyday Examples", topic: "Solid, Liquid and Gas", date: "16 Sep", score: "4 of 4", result: "Mastered" },
];

// A score counts first-time-correct answers; MAVIA re-teaches until a question
// is answered, so a low score means more practice, never a failed lesson.
export const SCORE_FOOTNOTE =
  "A score counts the questions answered correctly the first time they were asked. MAVIA re-teaches a lesson until it is answered, so a low score means more practice, never a failed lesson.";
