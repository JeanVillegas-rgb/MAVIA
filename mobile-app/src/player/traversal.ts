// The lesson player's traversal, as pure functions.
//
// The engine (backend/adaptive/services.py) makes every decision on the
// server and answers each graded question with one command: next_question,
// retry, escalate_variant, regress, resume, advance or complete. This module
// is the app's half: given where the student is and that command, decide what
// they hear and see next. It holds no React, no audio and no network, so it
// can be driven straight from a script against the live API -- see
// scripts/check-traversal.mjs.

import type { ApiCommand, ApiPackageStep, ApiReviewItem, ApiTopicPackage, ApiTrack, Variant } from "@/api/types";

// "continue" = a listen-only step (no questions) has been heard; the screen
// asks the server to move on (POST /mobile/topics/<id>/continue/).
export type Phase = "audio" | "questions" | "continue" | "done";

// Structurally identical to QuestionCard's `Question`, redeclared here so this
// module stays free of anything that imports React. The correct answer never
// reaches the phone, so it is always "".
export type PlayerQuestion = {
  id: number;
  order: number;
  prompt: string;
  question_type: string;
  choices: string[];
  correct_answer: string;
  audio_url: string;             // the question's recorded clip; "" when it has none
};

export type PlayerState = {
  topic: { id: number; title: string } | null;
  steps: ApiPackageStep[];
  position: number;              // the step (concept) being taught
  variant: Variant;              // which explanation type is playing
  returnTo: number | null;       // set while detoured through a prerequisite
  questionIndex: number;         // which of the step's questions is asked next
  // Whether the step has a question to ask after this listening. False after
  // a missed True/False with nothing fair left to ask: hear the re-teach, then
  // move on (a missed TF is never asked again).
  awaitingQuestion: boolean;
  trackIndex: number;            // which audio clip of the step is playing
  // Bumped every time a question is served, including the same question
  // re-served after a miss. Part of QuestionCard's key, so the card always
  // remounts fresh instead of keeping the answered state of the last attempt.
  askCount: number;
  phase: Phase;
  // Spoken hand-off played before the narration it introduces (the student
  // may not see the screen). Null when there is nothing to explain.
  announcement: string | null;
  // The topic was already finished before it was opened this time.
  finishedBefore: boolean;
};

export const INITIAL_STATE: PlayerState = {
  topic: null,
  steps: [],
  position: 1,
  variant: "standard",
  returnTo: null,
  questionIndex: 0,
  awaitingQuestion: true,
  trackIndex: 0,
  askCount: 0,
  phase: "audio",
  announcement: null,
  finishedBefore: false,
};

// --- reading the package ----------------------------------------------------

export function currentStep(state: PlayerState): ApiPackageStep | null {
  return state.steps.find((step) => step.position === state.position) ?? null;
}

/** "Concept 3 of 13": the package carries no titles, only audio. */
export function stepLabel(state: PlayerState): string {
  const index = state.steps.findIndex((step) => step.position === state.position);
  return `Concept ${index + 1} of ${state.steps.length}`;
}

/** The clips to play for this step, in the current explanation type --
 *  falling back to Standard if that type has no audio. */
export function tracksFor(state: PlayerState): ApiTrack[] {
  const step = currentStep(state);
  if (!step) return [];
  const urls = step.versions[state.variant] ?? step.versions.standard ?? Object.values(step.versions)[0] ?? [];
  const label = stepLabel(state);
  return urls.map((url, index) => ({
    id: `step-${step.position}-${state.variant}-${index}`,
    order: index,
    title: urls.length > 1 ? `${label} (part ${index + 1} of ${urls.length})` : label,
    type: "lesson_content",
    audio_url: url,
    audio_ready: Boolean(url),
    text: "",
  }));
}

function choicesOf(choices: string[] | Record<string, string> | null): string[] {
  if (!choices) return [];
  if (Array.isArray(choices)) return choices.map(String);
  return Object.keys(choices)
    .sort()
    .map((key) => String(choices[key]));
}

/** The step's questions, then its reserve (asked only after a missed True/False). */
function questionPool(step: ApiPackageStep) {
  return [...step.questions, ...(step.reserve_questions ?? [])];
}

export function questionsFor(state: PlayerState): PlayerQuestion[] {
  const step = currentStep(state);
  if (!step) return [];
  return questionPool(step).map((q, index) => ({
    id: q.id,
    order: index,
    prompt: q.text,
    question_type: q.format === "TF" ? "true_false" : "multiple_choice",
    choices: q.format === "TF" ? [] : choicesOf(q.choices),
    correct_answer: "",
    audio_url: step.question_audio?.[String(q.id)] ?? "",
  }));
}

/** QuestionCard's React key: a re-served question must never reuse the key of
 *  the attempt that was just answered, or the card stays answered/disabled. */
export function cardKey(state: PlayerState): string | null {
  const question = questionsFor(state)[state.questionIndex];
  if (!question) return null;
  return `${question.id}-${state.position}-${state.variant}-${state.askCount}`;
}

function indexOfQuestion(steps: ApiPackageStep[], position: number, questionId: number | null): number {
  const step = steps.find((s) => s.position === position);
  if (!step || questionId == null) return 0;
  const index = questionPool(step).findIndex((q) => q.id === questionId);
  return index >= 0 ? index : 0;
}

// --- spoken hand-offs -------------------------------------------------------
// Every change in what the student is about to hear announces itself first.
// QuestionCard has already said "Correct" or "Not quite", so none repeat it.

export const RETEACH_LINE: Record<Variant, string> = {
  standard: "Let's go over that idea again.",
  simplified: "Here's the same idea, explained more simply.",
  elaborated: "Let's go through that idea in more detail.",
};
export const DETOUR_LINE = "First, a quick review of something this builds on.";
export const RESUME_LINE = "Now, back to where you left off.";
export const ADVANCE_LINE = "Next idea.";

/** The end-of-segment review: each question the student missed and never got
 *  right, with its answer and why. Said only once the segment is over, so it can
 *  never give away a question still to come. Null when nothing was missed. */
export function reviewLine(review: ApiReviewItem[] | undefined): string | null {
  if (!review || review.length === 0) return null;
  const intro =
    review.length === 1
      ? "Before we go on, let's review the question you missed."
      : `Before we go on, let's review the ${review.length} questions you missed.`;
  const items = review.map((item) =>
    [item.question, `The answer is ${item.answer}.`, item.explanation].filter(Boolean).join(" ")
  );
  return [intro, ...items].join(" ");
}

function withReview(command: ApiCommand, line: string | null): string | null {
  return [reviewLine(command.review), line].filter(Boolean).join(" ") || null;
}

// --- transitions ------------------------------------------------------------

/** Where the student lands on opening a topic: wherever they left off. */
export function applyPackage(pkg: ApiTopicPackage): PlayerState {
  const base: PlayerState = {
    ...INITIAL_STATE,
    topic: pkg.topic,
    steps: pkg.steps,
    position: pkg.progress.current_step_position,
    variant: pkg.progress.current_variant || "standard",
    returnTo: pkg.progress.return_to_position,
    questionIndex: indexOfQuestion(pkg.steps, pkg.progress.current_step_position, pkg.next_question_id),
    awaitingQuestion: pkg.next_question_id != null,
  };
  if (pkg.progress.completed) return { ...base, phase: "done", finishedBefore: true };
  if (pkg.steps.length === 0) return { ...base, phase: "done" };
  return base;
}

/** The last clip of a step has finished playing. */
export function afterAudio(state: PlayerState): PlayerState {
  const step = currentStep(state);
  // No step loaded (the package hasn't arrived): nothing has been heard, so
  // never treat it as a listen-only step that is done.
  if (!step) return state;
  const ask = state.awaitingQuestion && questionPool(step).length > 0;
  return { ...state, phase: ask ? "questions" : "continue", announcement: null };
}

/** Follow one command from the engine (after an answer, or after continuing
 *  past a listen-only step). */
export function applyCommand(state: PlayerState, command: ApiCommand): PlayerState {
  if (command.action === "complete") {
    return { ...state, phase: "done", announcement: reviewLine(command.review) };
  }

  const position = command.next_step_position ?? state.position;
  const variant = (command.next_variant || state.variant) as Variant;
  const next: PlayerState = {
    ...state,
    position,
    variant,
    returnTo: command.return_to_position,
    questionIndex: indexOfQuestion(state.steps, position, command.next_question_id),
    awaitingQuestion: command.next_question_id != null,
    askCount: state.askCount + 1,
    announcement: null,
  };

  switch (command.action) {
    case "next_question": // right answer, more of this step to ask
    case "retry": //         wrong answer, ask it again
      return { ...next, phase: "questions" };
    case "escalate_variant": // re-teach in the next explanation type, then ask again
      return { ...next, trackIndex: 0, phase: "audio", announcement: RETEACH_LINE[variant] };
    case "regress": // detour through a prerequisite
      return { ...next, trackIndex: 0, phase: "audio", announcement: DETOUR_LINE };
    case "resume": // back from the detour
      return { ...next, trackIndex: 0, phase: "audio", announcement: withReview(command, RESUME_LINE) };
    case "advance": // next concept
    default:
      return { ...next, trackIndex: 0, phase: "audio", announcement: withReview(command, ADVANCE_LINE) };
  }
}
