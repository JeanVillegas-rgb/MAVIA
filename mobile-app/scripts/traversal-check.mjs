// Replay recorded student sessions through the app's own traversal module.
//
// The transcripts come from backend/adaptive/test_mobile_traversal.py, which
// drives the real student API under several answer policies and records every
// request/response pair exactly as a phone would receive it. This side feeds
// them through src/player/traversal.ts -- the same code the player screen runs
// -- and asserts the student is never left with nothing to do.
//
// Run both halves with `node scripts/check-traversal.mjs`.

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const TRANSCRIPTS = process.argv[2];
const MODULE = process.argv[3];

const {
  applyStart,
  applyResult,
  cardKey,
  questionsFor,
  tracksFor,
  RETEACH_LINE,
  CHUNK_SWITCH_LINE,
  DETOUR_LINE,
  RESUME_LINE,
  ADVANCE_LINE,
} = await import(`file://${MODULE}`);

const ANNOUNCEMENTS = new Set([
  ...Object.values(RETEACH_LINE),
  CHUNK_SWITCH_LINE,
  DETOUR_LINE,
  RESUME_LINE,
  ADVANCE_LINE,
]);

function currentQuestionId(state) {
  const q = questionsFor(state)[state.questionIndex];
  return q ? q.id : null;
}

const failures = [];
let checks = 0;

function check(scenario, step, label, condition, detail = "") {
  checks += 1;
  if (!condition) failures.push(`${scenario} @ step ${step}: ${label}${detail ? ` -- ${detail}` : ""}`);
}

for (const file of readdirSync(TRANSCRIPTS).filter((f) => f.endsWith(".json")).sort()) {
  const t = JSON.parse(readFileSync(join(TRANSCRIPTS, file), "utf8"));
  const scenario = t.scenario;

  let state = applyStart(t.lesson_package, t.start);
  let previousKey = null;
  const keysSeen = new Set();

  // The player must open on something playable, not an empty screen.
  check(
    scenario, "start", "opened with nothing to present",
    state.phase === "done" || tracksFor(state).length > 0 || questionsFor(state).length > 0
  );

  t.steps.forEach((entry, i) => {
    const { submitted, response } = entry;

    // I1. The app must be showing the question the engine thinks is assigned.
    // If these ever diverge the student answers one question and the server
    // grades another -- submit-response rejects it with 400 and the screen
    // dies on an error it cannot explain.
    check(
      scenario, i, "app is showing a different question than the engine assigned",
      currentQuestionId(state) === submitted.question_id,
      `app=${currentQuestionId(state)} engine=${submitted.question_id}`
    );

    // I2. The card key must be fresh for every presentation, so a re-served
    // question remounts re-enabled instead of staying stuck on its feedback.
    const key = cardKey(state);
    check(scenario, i, "question re-presented with a stale card key", key !== previousKey, `key=${key}`);
    check(scenario, i, "card key reused from an earlier presentation", !keysSeen.has(key), `key=${key}`);
    keysSeen.add(key);
    previousKey = key;

    const before = state;
    state = applyResult(state, response);

    if (response.completed) {
      check(scenario, i, "engine completed but the player did not", state.phase === "done");
      return;
    }

    // I3. Whenever the content itself changed, the student has to be walked
    // back through it before the question returns -- and told why.
    const variantChanged = state.variant !== before.variant;
    const chunkChanged = state.currentChunk !== before.currentChunk;
    const conceptChanged = state.pathStep?.concept_id !== before.pathStep?.concept_id;
    if (state.pathStep && (variantChanged || chunkChanged || conceptChanged)) {
      check(
        scenario, i, "new content was not presented before re-asking",
        state.phase === "audio",
        `phase=${state.phase} variant ${before.variant}->${state.variant}`
      );
      check(
        scenario, i, "content changed with no spoken hand-off",
        ANNOUNCEMENTS.has(state.announcement),
        `announcement=${JSON.stringify(state.announcement)}`
      );
    }

    // I4. A miss that re-serves the same question is the regression that
    // started all this: it must re-teach, not sit there.
    if (!submitted.intended_correct && response.next_question === submitted.question_id) {
      check(
        scenario, i, "same question re-served without re-presenting anything",
        state.phase === "audio" || state.askCount !== before.askCount,
        `phase=${state.phase} askCount ${before.askCount}->${state.askCount}`
      );
    }

    // I7. Every part of a split passage is queued, at whichever rung is
    // active. A concept the chunker cut in two used to play its first piece
    // only; the rest of the narration silently never happened.
    if (state.pathStep && state.phase === "audio") {
      const chunk = state.currentChunk != null
        ? state.pathStep.alternates.find((a) => a.learning_object_id === state.currentChunk)
        : null;
      const versions = (chunk ?? state.pathStep).versions;
      const version = versions[state.variant] ?? versions.normal;
      const expected = version?.parts?.length || 1;
      check(
        scenario, i, "split passage not queued as one track per part",
        tracksFor(state).length === expected,
        `tracks=${tracksFor(state).length} parts=${expected} variant=${state.variant}`
      );
    }

    // I5. Never a dead screen: whatever phase we land in must have content.
    if (state.phase === "questions") {
      check(
        scenario, i, "landed on the question phase with no question",
        Boolean(questionsFor(state)[state.questionIndex])
      );
    } else if (state.phase === "audio") {
      const track = tracksFor(state)[state.trackIndex];
      check(scenario, i, "landed on the audio phase with no track", Boolean(track));
      // A track with neither audio nor text would be a silent dead end; the
      // player only escapes it by falling through, which it can only do if
      // there is a question waiting.
      if (track && !track.audio_ready && !track.text) {
        check(
          scenario, i, "silent track with no question to fall through to",
          questionsFor(state).length > 0
        );
      }
    }
  });

  // I6. The run has to end somewhere.
  if (t.ended_completed) {
    check(scenario, "end", "transcript completed but the player is not done", state.phase === "done");
  }

  const audible = state.phase !== "done" ? "still going" : "done";
  console.log(`  ${failures.length ? " " : "✓"} ${scenario.padEnd(22)} ${String(t.steps.length).padStart(3)} answers -> ${audible}`);
}

console.log(`\n${checks} assertions across the replayed sessions`);
if (failures.length) {
  console.error(`\n${failures.length} FAILED:`);
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}
console.log("traversal OK");
