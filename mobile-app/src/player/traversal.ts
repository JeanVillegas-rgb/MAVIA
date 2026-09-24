// The lesson player's traversal, as pure functions.
//
// Everything here is the *app's* half of the adaptive ruling: given where the
// student is and what the engine just replied, decide what they see and hear
// next. It deliberately holds no React, no audio and no network so it can be
// driven straight from a test harness against real recorded API payloads --
// see scripts/traversal-check.mjs, which replays whole courses through it.
//
// The engine's half lives in backend/adaptive/services.py (AdaptiveEngine);
// backend/adaptive/PATH_MODE.md is the ruling both halves mirror.

import {
  ApiLesson,
  ApiStartResult,
  ApiStep,
  ApiStepQuestion,
  ApiStepVersions,
  ApiSubmitResult,
  ApiTrack,
  Variant,
} from "@/api/client";

export type Phase = "audio" | "questions" | "done";

// Structurally identical to QuestionCard's `Question`, redeclared here so this
// module stays free of anything that imports React.
export type PlayerQuestion = {
  id: number;
  order: number;
  prompt: string;
  question_type: string;
  choices: string[];
  correct_answer: string;
};

export type PlayerState = {
  lesson: ApiLesson | null;
  pathStep: ApiStep | null;
  variant: Variant;
  currentChunk: number | null;
  remediationTarget: number | null;
  questionIndex: number;
  trackIndex: number;
  // Bumped every time a question is served, including the same question id
  // re-served after a miss. It is part of QuestionCard's key, so the card
  // always remounts fresh (re-enabled, re-narrated) instead of keeping the
  // answered/disabled state left behind by the attempt that just failed.
  askCount: number;
  phase: Phase;
  // Spoken hand-off to play on entering the audio phase, before the narration
  // it introduces. Null when there is nothing to explain.
  announcement: string | null;
};

// --- learning-path ("path mode") adapters -----------------------------------
// A concept step is content-shaped like one track (one narration, one
// audio_url per variant) with 2 questions (1 LOT + 1 HOT) attached, rather
// than a lesson's whole playlist. These adapt it to the two shapes the player
// already knows how to render, so nothing downstream needs a second render
// path -- only where a step transitions to the next one differs.

// Which chunk's content/questions a step is currently showing: the
// representative (chunkId == null, the default) or one of its alternates --
// another uploaded PDF's own telling of the same concept, switched to by the
// engine once the representative's own ladder (normal/simplified/elaborated)
// is exhausted. Falls back to the representative if chunkId doesn't match
// any alternate, mirroring the backend's own _chunk_content fallback.
export function stepChunk(
  step: ApiStep,
  chunkId: number | null
): { versions: ApiStepVersions; questions: ApiStepQuestion[] } {
  if (chunkId != null) {
    const alt = step.alternates.find((a) => a.learning_object_id === chunkId);
    if (alt) return { versions: alt.versions, questions: alt.questions };
  }
  return { versions: step.versions, questions: step.questions };
}

// One track per part of the concept's passage, in reading order. A concept
// the chunker split into "(Part 1 of 2)" pieces used to reach the player as
// its first piece only -- the rest of the narration was never heard. The
// player already walks a multi-track playlist and moves to the questions after
// the last track, so a split concept needs nothing more than its parts.
export function stepTracks(step: ApiStep, variant: Variant, chunkId: number | null): ApiTrack[] {
  const { versions } = stepChunk(step, chunkId);
  const version = versions[variant] ?? versions.normal;
  const parts = version?.parts?.length
    ? version.parts
    : [{ text: version?.text ?? "", audio_url: version?.audio_url ?? "" }];
  return parts.map((part, index) => ({
    id: `step-${step.position}-${chunkId ?? "representative"}-${variant}-${index}`,
    order: index,
    title: parts.length > 1 ? `${step.title} (part ${index + 1} of ${parts.length})` : step.title,
    type: "lesson_content",
    audio_url: part.audio_url ?? "",
    audio_ready: Boolean(part.audio_url),
    text: part.text ?? "",
  }));
}

// GeneratedQuestion never carries a correct_answer to the student (see
// learning_path/HANDOFF.md § 3), so the mapped correct_answer is always "" --
// QuestionCard's per-option "this was correct" highlight simply never
// matches, which is the desired behavior here, not a bug.
export function stepQuestions(step: ApiStep, chunkId: number | null): PlayerQuestion[] {
  const { questions } = stepChunk(step, chunkId);
  return questions.map((q, index) => ({
    id: q.id,
    order: index,
    prompt: q.text,
    question_type: q.format === "TF" ? "true_false" : "multiple_choice",
    choices:
      q.format === "MCQ" && q.choices
        ? Object.keys(q.choices)
            .sort()
            .map((key) => q.choices![key])
        : [],
    correct_answer: "",
  }));
}

export function questionsFor(state: PlayerState): PlayerQuestion[] {
  if (state.pathStep) return stepQuestions(state.pathStep, state.currentChunk);
  return state.lesson?.questions ?? [];
}

/** QuestionCard's React key. Two consecutive presentations of a question must
 *  never share one: the engine re-serves the same question id after a miss,
 *  and a reused key makes React keep the card that is already answered and
 *  disabled, which is what used to leave a student stuck on "Not quite" with
 *  no way to respond to the re-teach. Exported so the screen and the test
 *  harness cannot drift apart on what counts as a new presentation. */
export function cardKey(state: PlayerState): string | null {
  const question = questionsFor(state)[state.questionIndex];
  if (!question) return null;
  return `${question.id}-${state.variant}-${state.currentChunk ?? "rep"}-${state.askCount}`;
}

export function tracksFor(state: PlayerState): ApiTrack[] {
  if (state.pathStep) return stepTracks(state.pathStep, state.variant, state.currentChunk);
  return state.lesson?.tracks ?? [];
}

// --- spoken hand-offs -------------------------------------------------------
// Every transition that changes what the student is about to hear announces
// itself before the narration starts. This is the only channel that carries
// it: the student cannot see the screen. QuestionCard has already said
// "Correct" or "Not quite" by the time one of these plays, so none repeat it.

export const RETEACH_LINE: Record<Variant, string> = {
  normal: "Let's go over that idea again.",
  simplified: "Here's the same idea, explained more simply.",
  elaborated: "Let's go through that idea in more detail.",
};
export const CHUNK_SWITCH_LINE = "Here's another explanation of the same idea.";
export const DETOUR_LINE = "First, a quick review of something this builds on.";
export const RESUME_LINE = "Now, back to where you left off.";
export const ADVANCE_LINE = "Next idea.";

export const INITIAL_STATE: PlayerState = {
  lesson: null,
  pathStep: null,
  variant: "normal",
  currentChunk: null,
  remediationTarget: null,
  questionIndex: 0,
  trackIndex: 0,
  askCount: 0,
  phase: "audio",
  announcement: null,
};

/** Where the student lands on opening a lesson: a resumed path step, or the
 *  lesson's own playlist / flat question list. */
export function applyStart(pkg: ApiLesson, start: ApiStartResult | null): PlayerState {
  const step = start?.current_step ?? null;
  // A path step belongs to whichever topic the engine is on. If that is not
  // the package we fetched, follow the engine's -- rendering its concept
  // under another lesson's title and playlist would be showing two different
  // topics at once.
  const lesson = (step && start?.lesson) || pkg;
  const base: PlayerState = { ...INITIAL_STATE, lesson, pathStep: step };

  if (step) {
    const variant = start!.learning_state.current_variant || "normal";
    const currentChunk = start!.learning_state.current_chunk ?? null;
    const assignedId = start!.learning_state.current_generated_question;
    const active = stepChunk(step, currentChunk).questions;
    const idx = active.findIndex((q) => q.id === assignedId);
    return {
      ...base,
      variant,
      currentChunk,
      questionIndex: idx >= 0 ? idx : 0,
      remediationTarget: start!.learning_state.remediation_target_position ?? null,
      // Always via "audio": a version with no generated audio gets read aloud
      // by the device rather than parking the student on a warning they
      // cannot see.
      phase: "audio",
    };
  }

  if (lesson.tracks.length === 0) {
    return { ...base, phase: lesson.has_questions ? "questions" : "done" };
  }
  return base;
}

/** The state transition for one graded answer. `res` is null for open-ended
 *  questions, which are never submitted and so never graded. */
export function applyResult(state: PlayerState, res: ApiSubmitResult | null): PlayerState {
  const questions = questionsFor(state);

  if (!res) {
    // Nothing was graded, so the engine has no opinion -- step locally
    // through this topic's own list, as the player did before path mode.
    if (state.questionIndex + 1 < questions.length) {
      return {
        ...state,
        questionIndex: state.questionIndex + 1,
        askCount: state.askCount + 1,
        announcement: null,
      };
    }
    return { ...state, pathStep: null, phase: "done", announcement: null };
  }

  // The engine may have moved on to a different topic entirely (this one's
  // chunks fully consumed) -- swap in its fresh package rather than rendering
  // content belonging to wherever the student started, so the journey stays
  // continuous instead of stalling at the topic boundary.
  const lessonChanged = Boolean(res.lesson) && res.lesson!.id !== state.lesson?.id;
  const lesson = lessonChanged ? res.lesson : state.lesson;

  if (res.completed) {
    return { ...state, lesson, pathStep: null, phase: "done", announcement: null };
  }

  if (res.current_step) {
    const newStep = res.current_step;
    const sameConcept = state.pathStep?.concept_id === newStep.concept_id;
    const variant = res.current_variant ?? "normal";
    const currentChunk = res.current_chunk ?? null;
    const chunkChanged = currentChunk !== state.currentChunk;
    const variantChanged = variant !== state.variant;
    const remediationTarget = res.remediation_target_position ?? null;
    const wasDetoured = state.remediationTarget !== null;

    const active = stepChunk(newStep, currentChunk).questions;
    const idx = active.findIndex((q) => q.id === res.next_question);

    const next: PlayerState = {
      ...state,
      lesson,
      pathStep: newStep,
      variant,
      currentChunk,
      remediationTarget,
      questionIndex: idx >= 0 ? idx : 0,
      askCount: state.askCount + 1,
      announcement: null,
    };

    // Same concept, same chunk, same variant: the LOT -> HOT move within a
    // question just cleared. Nothing new to hear, so go straight to asking it.
    if (sameConcept && !chunkChanged && !variantChanged) {
      return { ...next, phase: "questions" };
    }

    // Everything else hands the student genuinely different material: an
    // escalated variant of this same explanation (the miss path -- normal ->
    // simplified -> elaborated, the same question waiting at the end of it),
    // another PDF's telling of the concept, or a different concept entirely.
    // All three get played before the question is put again -- re-asking
    // without first re-teaching is what turned a miss into a dead end.
    const announcement = !sameConcept
      ? remediationTarget !== null
        ? DETOUR_LINE
        : wasDetoured
        ? RESUME_LINE
        : ADVANCE_LINE
      : chunkChanged
      ? CHUNK_SWITCH_LINE
      : RETEACH_LINE[variant];

    return { ...next, trackIndex: 0, phase: "audio", announcement };
  }

  // No path step in the result: either this topic was always legacy mode
  // (flat lessons.Question list), or the engine just handed off from path
  // mode into a legacy-mode topic. Either way, follow it rather than
  // stopping -- find the assigned question in the (possibly fresh) lesson
  // package and keep going.
  const wasPathMode = state.pathStep !== null;
  const nextQuestions = lesson?.questions ?? [];
  const idx = nextQuestions.findIndex((q) => q.id === res.next_question);

  const next: PlayerState = {
    ...state,
    lesson,
    pathStep: null,
    variant: "normal",
    currentChunk: null,
    remediationTarget: null,
    announcement: null,
  };

  if (idx < 0 && res.next_question == null) {
    return { ...next, phase: "done" };
  }

  // Legacy mode re-serves the same question id after a miss too (see
  // AdaptiveEngine.evaluate: it only advances once the answer is right or
  // MAX_QUESTION_ATTEMPTS is spent), so the card needs the same forced
  // remount the path-mode branch above takes.
  const served: PlayerState = {
    ...next,
    questionIndex: idx >= 0 ? idx : 0,
    askCount: state.askCount + 1,
  };

  if (wasPathMode || lessonChanged) {
    const hasTracks = (lesson?.tracks?.length ?? 0) > 0;
    return { ...served, trackIndex: 0, phase: hasTracks ? "audio" : "questions" };
  }
  return served;
}
