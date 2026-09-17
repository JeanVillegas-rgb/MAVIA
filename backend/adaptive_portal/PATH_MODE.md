# Path mode: the learning path drives the mobile course package

Added 2026-09-15, pulling `course` / `question_generation` / `learning_path`
content into the mobile-facing engine. See `learning_path/HANDOFF.md` for the
path contract itself; this note is the `adaptive_portal` side of using it.

## Why two modes

`adaptive_portal` is the mobile app's enrollment-based engine. Before this change it only knew
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
- On repeated failure (`MAX_STEP_QUESTION_ATTEMPTS`), the learner is sent
  through the step's **nearest prerequisite** as a refresher, then back to
  where they struggled — not just stepped down in place. A step with no
  prerequisite to fall back on is re-taught in a plainer variant instead
  (`current_variant`: normal → simplified → elaborated), same idea as the
  legacy engine's difficulty step-down, applied to content instead.

A topic that has never been published with a path still runs the original
**legacy mode** unchanged — nothing about it changed in this pass. Which mode
a `LearningState` is in is determined by which fields are populated:

| | legacy | path |
|---|---|---|
| current question | `current_question` (`lessons.Question`) | `current_generated_question` (`GeneratedQuestion`) |
| position within topic | *(none — flat list)* | `current_step_position` |
| mid-remediation marker | *(none)* | `remediation_target_position` |
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
- `POST /api/adaptive-portal/start/` and `.../submit-response/` return a
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
- Remediation goes one level deep: if the prerequisite step's own refresher
  question is also missed `MAX_STEP_QUESTION_ATTEMPTS` times, the engine does
  not chain into a *second* prerequisite — it re-teaches that step in a
  plainer variant instead. Revisit if real usage shows that's too shallow.
