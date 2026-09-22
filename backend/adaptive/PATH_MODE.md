# Path mode: the learning path drives the mobile course package

Added 2026-09-15, pulling `course` / `question_generation` / `learning_path`
content into the mobile-facing engine. See `learning_path/HANDOFF.md` for the
path contract itself; this note is the `adaptive` side of using it.

## Why two modes

`adaptive` is the mobile app's engine. Before this change it only knew
one way to walk a course: flatten every topic's `lessons.Question` rows in PDF
order and push the learner forward, occasionally stepping down after repeated
misses. That's a flat list, so "escalation" really was just tier up / tier
down within it.

Once a topic has been published with a learning path
(`learning_path.services.get_published_path`), it isn't a flat list any more —
it's concepts in prerequisite order, some of them depending on earlier ones.
**Path mode** is the engine that understands that shape:

- Steps are walked by `position`, not by `lessons.Question.order`.
- Questions come from `question_generation.GeneratedQuestion` (richer:
  Bloom level, thinking order, difficulty) via the path payload, not
  `lessons.Question`.
- **The ladder reacts on every miss, not after `MAX_STEP_QUESTION_ATTEMPTS`
  identical ones.** A wrong answer escalates the content shown before the
  *same* question one rung (`current_variant`: normal → simplified →
  elaborated). Only once elaborated has also failed does anything structural
  happen — see `_reroute` in `adaptive/services.py`:
  1. Detour through the step's **nearest prerequisite** as a refresher (the
     bigger intervention, tried first), bounded two ways: by
     `MAX_REMEDIATION_DEPTH` nested detours (`LearningState.remediation_stack`)
     so a struggling learner is never walked arbitrarily far back through the
     prerequisite graph, *and* by **one detour per step, ever**
     (`LearningState.remediated_positions`).

     Both bounds are needed, and the second is not obvious. The stack is
     *popped* when a detour is resumed, so depth alone never catches the
     case where a step keeps failing right after coming back from its own
     refresher: stack empty → detour allowed → resume → fail → stack empty
     again. With two concepts that is a closed loop a learner answering
     wrong can never leave. `remediated_positions` records which positions
     have already spent the remedy; it is reset on a topic change, since
     positions are numbered per topic.
  2. Once no prerequisite is available (a leaf concept, or the depth cap is
     reached): switch to this step's **alternate chunk** if one exists and
     hasn't been tried yet — a different uploaded PDF's own, independently
     written explanation of the same concept, tracked via
     `LearningState.current_chunk` (`None` = the step's representative).
     See `learning_path/HANDOFF.md`'s `alternates` field.
  3. **A second pass through the ladder**, but only for a step that had no
     structural remedy available at all — no prerequisite *and* no alternate.
     Every other step gets the ladder plus one structural intervention; a
     root concept would otherwise get the ladder alone and then be pushed
     forward into material that depends on the very concept just missed.
     So it is taught again from `normal`. Bounded by `MAX_LADDER_PASSES`
     (2), counted with `current_question_attempts`, which every real move
     resets — so it only ever measures time spent on the step in hand.
  4. Nothing left to try: give up rerouting this step and move the learner
     on (`_resume_or_advance` → `_advance_past`) — the same "never strand
     anyone" guarantee the legacy engine already has. `_advance_past` scans
     forward for a step that still has an *unanswered* question; a step the
     learner already cleared is stepped over, not re-entered with nothing
     to ask.
- **Resuming after a detour clears** prefers a step's unused alternate chunk
  over repeating what already failed (starting that alternate fresh at
  `normal`); only once both chunks are exhausted does it resume at the last
  variant it had reached (`elaborated`) rather than re-walking the ladder
  from `normal` on content already known not to work.

A topic that has never been published with a path still runs the original
**legacy mode** unchanged — nothing about it changed in this pass. Which mode
a `LearningState` is in is determined by which fields are populated:

| | legacy | path |
|---|---|---|
| current question | `current_question` (`lessons.Question`) | `current_generated_question` (`GeneratedQuestion`) |
| position within topic | *(none — flat list)* | `current_step_position` |
| detour chain | *(none)* | `remediation_stack` — list of `{position, chunk_id}` to resume, nearest last. The API still reports just `remediation_target_position` (its top), since no client needs the whole chain. |
| detours already spent | *(none)* | `remediated_positions` — step positions that have used their one prerequisite detour in this topic. Bounds *repetition*, where `remediation_stack` bounds *depth*. |
| active chunk | *(none)* | `current_chunk` — `None` for the step's representative, else the alternate `LearningObject` currently in use |
| content difficulty | *(none)* | `current_variant` |

Exactly one of `current_question` / `current_generated_question` is set at a
time. `resolve_learning_start(course)` is what decides, per topic in course
order, which mode a fresh `LearningState` starts in — it tries every topic's
published path first, falling back to the flat walk. `resolve_start` (the
original function) is untouched and still exactly the legacy walk; it's kept
for its own tests and any caller that only ever wants that.

## Where this shows up

- `lessons/services/lesson_package.py::build_lesson_payload` now includes a
  `learning_path` key (student-safe: no answers) alongside the existing
  tracks/questions, so any client reading one lesson's package also gets the
  concept order and prerequisite structure without a second request.
- `POST /api/adaptive/start/` and `.../submit-response/` return a
  `current_step` key (student-safe) whenever the learner is in path mode, in
  addition to the existing `lesson` key.
- `StudentResponse` now has both `question` and `generated_question` FKs
  (exactly one set — enforced by a check constraint) so a course's response
  history works whichever mode each answer was given in.

## What this doesn't do (yet)

- The legacy engine's own cross-topic walk (`_next_step` /
  `ordered_course_steps`) still only discovers topics that have
  `lessons.Question` rows — it will skip a path-only topic. Path mode's own
  cross-topic walk (`_next_topic_with_content`) does not have this limitation
  and picks up either kind of topic. Unifying the two walks is future work if
  a course ever mixes legacy-only and path-only topics back to back.
- Chunk-switching only ever tracks one "have we used the alternate yet" bit
  per step (`LearningState.current_chunk`). A concept taught by 3+ PDFs only
  ever reaches the first alternate; the rest are never tried. Revisit if a
  real topic has that many independent tellings of one concept.
- The mobile lesson player (`app/(student)/home/[courseId]/[lessonId].tsx`)
  reads the active chunk via its own `stepChunk()` helper, kept in sync with
  `ApiLearningState.current_chunk`/`ApiSubmitResult.current_chunk` on every
  load and every submit. A chunk switch (or any other genuinely new content —
  advancing, detouring, resuming) routes back through the audio phase so the
  student hears/reads it before being asked its question; a plain variant
  escalation on the same chunk stays on the question they're already
  answering. The mobile client also follows the engine across a topic
  boundary either direction (path -> path, path -> legacy, legacy -> legacy):
  `evaluate_path`'s `next_question` falls back to `current_question_id` when
  `_advance_past` hands off into a legacy-mode topic (previously `None`,
  since only `current_generated_question_id` was surfaced), and the client
  no longer reads a missing `current_step` as "the whole course is done" —
  only `completed` means that. A `current_step: null` result with
  `completed: false` means "this topic's next question is in the (fresh)
  `lesson` payload instead," and the client swaps to that lesson's package
  and keeps going. Fixed after this was reported as "the lesson stops on one
  question segment, it needs to be continuous until all chunks consumed" —
  see `test_finishing_a_path_mode_topic_hands_off_to_the_next_legacy_topic`.
- Each concept's "normal" variant now carries real audio too (previously
  hardcoded to `""` in `_versions()` -- see `learning_path/services/published.py`
  and `learning_path/HANDOFF.md`), and `question_generation` isn't trusted to
  cap itself at one final question per `thinking_order`, so `_questions()`
  caps a step at the earliest LOT + earliest HOT. Between these,
  `QuestionCard.tsx` (mobile) no longer renders a manual "Next question"
  button at all -- narration (prompt+choices, the tapped choice, then
  correct/incorrect) drives `onNext()` itself via `expo-speech`'s `onDone`,
  with `useNarration`'s timeout fallback as the only safety net if a
  platform's TTS callback never fires. Reported as "why am I still pressing
  a next question button, I told you to make this automatic."
