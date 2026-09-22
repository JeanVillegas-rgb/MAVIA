"""Adaptive progression engine.

Adapter port of ``Milestone1-Jean/backend/adaptive/services.py``. The BKT
mastery update is kept verbatim; the Bloom-tier question selection is replaced
with a plain linear walk over ``lessons.Question.order`` because mavia's
question model carries no Bloom / difficulty axis.

**Two progression modes, chosen per topic:**

- **Path mode** -- the topic (``OutlineNode``) has a published learning path
  (see ``learning_path/HANDOFF.md``). Steps are walked in prerequisite order;
  a student stuck on a step is sent through its nearest prerequisite step
  before returning, instead of just being pushed forward. Questions come from
  ``question_generation.GeneratedQuestion`` via the path payload.
- **Legacy (flat) mode** -- no published path yet. The original plain walk
  over ``lessons.Question.order`` below, unchanged, so older content that was
  never run through the learning-path pipeline keeps working.

``LearningState`` carries fields for both; exactly one set is populated at a
time (``current_question`` for legacy, ``current_step_position`` +
``current_generated_question`` for path mode). See ``_evaluate_path`` /
``evaluate`` on ``AdaptiveEngine``.
"""

from django.utils import timezone

from lessons.models import OutlineNode, Question
from adaptive_config.models import AdaptiveConfig
from learning_path.services import get_published_path
from question_generation.models import GeneratedQuestion

from .models import StudentResponse

# Bayesian Knowledge Tracing parameters (unchanged from the Jean branch).
P_GUESS = 0.20
P_SLIP = 0.10
P_LEARN = 0.15
MASTERY_CEILING = 0.99
STARTING_MASTERY = 0.30

# Move a learner past a question they keep missing after this many attempts
# rather than stranding them on a thin question pool.
MAX_QUESTION_ATTEMPTS = 3

_LETTERS = ["a", "b", "c", "d", "e", "f"]


# ---------------------------------------------------------------------------
# course traversal
# ---------------------------------------------------------------------------

def top_level_ancestor(node):
    """The module (root outline node) a topic belongs to."""
    current = node
    while current is not None and current.parent_id is not None:
        current = current.parent
    return current


def lesson_questions(lesson_node):
    """Ordered questions for a topic: every Question whose material hangs off
    this outline node. De-duplicated, stable order."""
    return list(
        Question.objects.filter(material__outline_node=lesson_node)
        .order_by("order", "id")
        .distinct()
    )


def ordered_course_steps(course):
    """Flatten a course into an ordered list of teachable steps.

    Returns ``[(module_node, lesson_node, [questions])]`` for every non-root
    outline node that has at least one question, ordered the way a learner
    would walk them: by module order, then topic order within the module.
    """
    nodes = list(
        OutlineNode.objects.filter(course=course, parent__isnull=False, published=True)
        .select_related("parent")
        .order_by("depth", "order", "id")
    )
    module_order = {
        node.id: (node.order, node.id)
        for node in OutlineNode.objects.filter(course=course, parent__isnull=True)
    }

    steps = []
    for node in nodes:
        module = top_level_ancestor(node)
        if module is None:
            continue
        questions = lesson_questions(node)
        if not questions:
            continue
        steps.append((module_order.get(module.id, (0, module.id)), module, node, questions))

    steps.sort(key=lambda item: (item[0], item[2].order, item[2].id))
    return [(module, node, questions) for _key, module, node, questions in steps]


def resolve_start(course):
    """(module_node, lesson_node, question) to serve first, any of which may
    be ``None`` when the course has no question content yet."""
    steps = ordered_course_steps(course)
    if not steps:
        first_module = (
            OutlineNode.objects.filter(course=course, parent__isnull=True)
            .order_by("order", "id")
            .first()
        )
        return first_module, None, None
    module, node, questions = steps[0]
    return module, node, questions[0]


def _next_step(course, lesson_node, question):
    """The step after ``question`` within ``lesson_node``: the next question
    here, else the first question of the next topic, else ``(None, None, None)``
    meaning the course is finished."""
    steps = ordered_course_steps(course)

    for index, (module, node, questions) in enumerate(steps):
        if lesson_node is not None and node.id != lesson_node.id:
            continue
        q_ids = [q.id for q in questions]
        if question is not None and question.id in q_ids:
            pos = q_ids.index(question.id)
            if pos + 1 < len(questions):
                return module, node, questions[pos + 1]
        # fall through to the next step
        for next_module, next_node, next_questions in steps[index + 1:]:
            if next_questions:
                return next_module, next_node, next_questions[0]
        return None, None, None

    # current pointer no longer matches any step (content changed) — restart.
    module, node, q = resolve_start(course)
    return module, node, q


# ---------------------------------------------------------------------------
# answer checking
# ---------------------------------------------------------------------------

def _norm(value):
    return str(value or "").strip().lower()


def _acceptable_answers(question):
    """Every normalized string that should count as correct for ``question``."""
    correct = _norm(question.correct_answer)
    accepted = {correct}
    choices = question.choices or []

    if isinstance(choices, dict):
        choices = [choices.get(letter.upper()) or choices.get(letter) for letter in _LETTERS]
        choices = [c for c in choices if c]

    # correct answer given as a letter -> also accept the choice text
    if correct in _LETTERS and len(correct) == 1:
        idx = _LETTERS.index(correct)
        if idx < len(choices):
            accepted.add(_norm(choices[idx]))

    # correct answer given as text -> also accept its letter
    for idx, choice in enumerate(choices):
        if _norm(choice) == correct and idx < len(_LETTERS):
            accepted.add(_LETTERS[idx])

    if question.question_type == Question.Type.TRUE_FALSE:
        if correct in {"true", "t", "a"}:
            accepted.update({"true", "t", "a"})
        elif correct in {"false", "f", "b"}:
            accepted.update({"false", "f", "b"})

    return {a for a in accepted if a}


def answer_is_correct(question, selected_answer):
    selected = _norm(selected_answer)
    if not selected:
        return False
    accepted = _acceptable_answers(question)
    if selected in accepted:
        return True

    # selected given as a letter -> resolve to its choice text and retry
    choices = question.choices or []
    if isinstance(choices, dict):
        choices = [choices.get(letter.upper()) or choices.get(letter) for letter in _LETTERS]
        choices = [c for c in choices if c]
    if selected in _LETTERS and len(selected) == 1:
        idx = _LETTERS.index(selected)
        if idx < len(choices) and _norm(choices[idx]) in accepted:
            return True
    return False


# ---------------------------------------------------------------------------
# path traversal (learning_path prerequisites) -- see learning_path/HANDOFF.md
# ---------------------------------------------------------------------------

# Unlike the legacy engine's flat MAX_QUESTION_ATTEMPTS, path mode reacts on
# every miss rather than batching three identical ones before doing anything:
# variant escalates on attempt 1 and 2 (normal -> simplified -> elaborated),
# and a third miss on the same question -- now at "elaborated", nowhere left
# to escalate to -- is what triggers a reroute. This constant documents that
# ladder length; it isn't a counter compared against elsewhere in this file.
MAX_STEP_QUESTION_ATTEMPTS = 3

# How many times a single step may walk the whole variant ladder before the
# engine stops teaching it. Only a step with no structural remedy available at
# all -- no prerequisite to detour through, no alternate chunk to switch to --
# ever reaches a second pass; see _reroute. It exists so a learner who misses
# the *root* concept is taught it again rather than pushed straight into
# material that depends on the very thing they just missed. Counted with
# LearningState.current_question_attempts, which every real move resets, so it
# only ever measures time spent on the step in hand.
MAX_LADDER_PASSES = 2

# How many nested prerequisite detours a single reroute chain may hold at
# once (LearningState.remediation_stack). Bounds how far back a struggling
# learner can be walked before the engine gives up on rerouting and falls
# back to this step's alternate chunk instead -- an unbounded chain has no
# guaranteed floor and risks marching someone through the whole prerequisite
# graph on one hard concept.
MAX_REMEDIATION_DEPTH = 2

VARIANT_ORDER = ["normal", "simplified", "elaborated"]


def published_path_for(node):
    """The topic's published learning path, answers included -- this backend
    grades against it, so it needs them. ``None`` if never published."""
    if node is None:
        return None
    return get_published_path(node, include_answers=True)


def _step_by_position(path, position):
    return next((step for step in path["steps"] if step["position"] == position), None)


def _position_of_concept(path, concept_id):
    step = next((s for s in path["steps"] if s["concept_id"] == concept_id), None)
    return step["position"] if step else None


def _chunk_content(step, chunk_id):
    """``(versions, questions)`` for one learning object's telling of `step`'s
    concept. ``chunk_id=None`` means the step's representative (the default,
    and the only option before chunk-switching existed); otherwise the
    matching entry in ``step["alternates"]``. Falls back to the representative
    if `chunk_id` no longer matches anything (e.g. a republish dropped that
    alternate) rather than raising mid-assessment."""
    if chunk_id is not None:
        for alt in step.get("alternates", []):
            if alt["learning_object_id"] == chunk_id:
                return alt["versions"], alt["questions"]
    return step["versions"], step["questions"]


def _unused_alternate(step, chunk_id):
    """The one alternate chunk still worth trying for `step`, or ``None``.

    Only tracks a single "has this step switched chunks yet" bit
    (``LearningState.current_chunk``), so with more than one alternate PDF for
    the same concept only the first is ever reached. Same shape as
    PATH_MODE.md's other "one level deep" limitations -- revisit if a topic
    with 3+ independent tellings of one concept shows up in practice.
    """
    alternates = step.get("alternates") or []
    if not alternates or chunk_id is not None:
        return None
    return alternates[0]


def _ordered_step_questions(step, chunk_id=None):
    """LOT before HOT, then id -- a step holds one concept's questions, so
    this plays the role TIER_BUCKETS plays for the legacy engine without
    needing bucket machinery."""
    _versions, questions = _chunk_content(step, chunk_id)
    return sorted(questions, key=lambda q: (q["thinking_order"] == "HOT", q["id"]))


def _next_question_in_step(step, exclude_ids, chunk_id=None):
    for question in _ordered_step_questions(step, chunk_id):
        if question["id"] not in exclude_ids:
            return question
    return None


def _next_step_with_question(path, after_position, learning_state=None):
    """The first step after ``after_position`` that still has something to ask.

    With ``learning_state``, "to ask" excludes questions the learner has
    already answered correctly. Without that, a step they cleared on an
    earlier pass still looks like somewhere to send them: it *has* questions,
    they are just all spent. ``_advance_past`` would then pick it, ask
    ``_next_question_in_step`` for one, get ``None`` back and crash
    subscripting it -- a 500 on submit-response, mid-lesson.
    """
    for step in path["steps"]:
        if step["position"] <= after_position:
            continue
        exclude = _step_answered_ids(learning_state, step) if learning_state is not None else set()
        if _next_question_in_step(step, exclude) is not None:
            return step
    return None


def resolve_path_start(node):
    """``(path, step, question)`` to serve first for a topic's learning path.

    ``path`` is ``None`` when the topic has never been published with one --
    the caller falls back to the legacy flat walk in that case. A path with
    no answerable step anywhere returns ``(path, None, None)``.
    """
    path = published_path_for(node)
    if path is None:
        return None, None, None
    for step in path["steps"]:
        question = _next_question_in_step(step, exclude_ids=())
        if question is not None:
            return path, step, question
    return path, None, None


# True/False answers arrive from the student's device as "a"/"b", not spelled
# out. The braille numpad has one physical key per letter, so a two-option
# True/False question has to land on the same two keys as the first two
# options of a multiple-choice one -- otherwise the same finger position means
# different things from question to question. GeneratedQuestion stores TF
# answers as the words "True"/"False" with no choices dict, so the letters
# have to be accepted here rather than translated on the device (which never
# holds a correct answer to translate against). The legacy engine already does
# the same, in _acceptable_answers.
# See mobile-app/src/components/QuestionCard.tsx::optionsFor.
_TRUE_FORMS = {"true", "t", "a"}
_FALSE_FORMS = {"false", "f", "b"}
_TF_WORDS = {"true", "t", "false", "f"}


def path_answer_is_correct(question, selected_answer):
    """``question`` is one entry of a step's ``questions`` list (a dict from
    ``get_published_path``, not a ``GeneratedQuestion`` row)."""
    correct = str(question["correct_answer"] or "").strip()
    selected = str(selected_answer or "").strip()
    if not selected:
        return False
    if selected.upper() == correct.upper():
        return True

    # Gated on the question actually being True/False: an MCQ's correct answer
    # is a bare letter, and "A" would otherwise look like the word "true".
    is_tf = (question.get("format") or "").upper() == "TF" or correct.lower() in _TF_WORDS
    if is_tf:
        return any(
            correct.lower() in forms and selected.lower() in forms
            for forms in (_TRUE_FORMS, _FALSE_FORMS)
        )

    choices = question.get("choices") or {}
    if isinstance(choices, dict):
        for letter, text in choices.items():
            if letter.upper() == correct.upper() and str(text).strip().lower() == selected.lower():
                return True
    return False


def _step_answered_ids(learning_state, step, chunk_id=None):
    """Ids of this step's questions (for the given chunk) the learner already
    has correct on record -- used so re-entering a step (e.g. resuming after a
    prerequisite detour, or switching to an alternate chunk) does not re-ask
    something already cleared."""
    _versions, questions = _chunk_content(step, chunk_id)
    step_ids = {q["id"] for q in questions}
    return set(
        learning_state.responses.filter(
            generated_question_id__in=step_ids,
            is_correct=True,
            created_at__gte=learning_state.attempt_started_at,
        ).values_list("generated_question_id", flat=True)
    )


def _escalate_variant(current_variant):
    index = VARIANT_ORDER.index(current_variant) if current_variant in VARIANT_ORDER else 0
    return VARIANT_ORDER[min(index + 1, len(VARIANT_ORDER) - 1)]


def student_safe_question(question):
    """A step question dict with the answer key stripped -- for anything
    sent to a student's device. See learning_path/HANDOFF.md § 3."""
    return {k: v for k, v in question.items() if k not in ("correct_answer", "explanation")}


def student_safe_step(step):
    return {
        **step,
        "questions": [student_safe_question(q) for q in step["questions"]],
        "alternates": [
            {**alt, "questions": [student_safe_question(q) for q in alt["questions"]]}
            for alt in step.get("alternates", [])
        ],
    }


def _course_topic_nodes(course):
    """Every topic ``OutlineNode`` in course-teaching order (module order,
    then topic order), regardless of which question system it uses -- the
    superset ``ordered_course_steps`` narrows to lessons.Question-only."""
    nodes = list(
        OutlineNode.objects.filter(course=course, parent__isnull=False, published=True)
        .select_related("parent")
        .order_by("depth", "order", "id")
    )
    module_order = {
        node.id: (node.order, node.id)
        for node in OutlineNode.objects.filter(course=course, parent__isnull=True)
    }

    def sort_key(node):
        module = top_level_ancestor(node)
        head = module_order.get(module.id, (0, module.id)) if module else (0, 0)
        return (head, node.order, node.id)

    return sorted(nodes, key=sort_key)


def _next_topic_with_content(course, current_node):
    """The next topic after ``current_node`` with lessons.Question content
    or a published learning path -- whichever a learner reaches next,
    regardless of which system serves it. ``None`` past the last one."""
    nodes = _course_topic_nodes(course)
    ids = [node.id for node in nodes]
    start = ids.index(current_node.id) + 1 if current_node and current_node.id in ids else 0
    for node in nodes[start:]:
        if lesson_questions(node) or published_path_for(node) is not None:
            return node
    return None


def _enter_topic(node):
    """``(mode, step, question)`` for the first content of ``node``: path
    mode if it has a published, answerable path, else the legacy flat walk's
    first question (``question`` is ``None`` if it turns out to have neither
    -- callers only reach here after confirming it has one)."""
    path, step, question = resolve_path_start(node)
    if path is not None and step is not None:
        return "path", step, question
    questions = lesson_questions(node)
    return "legacy", None, (questions[0] if questions else None)


def start_topic(learning_state, node, *, fresh_attempt=False):
    """Point ``learning_state`` at the start of ``node``. Returns ``False`` if
    the topic has nothing to teach.

    ``fresh_attempt`` resets what counts as "already cleared". Pass it when
    replaying something finished, so the learner is actually taught again
    rather than advanced straight back to the end. Leave it off when merely
    switching topics, so returning to a half-finished one picks up roughly
    where it was left instead of starting over.

    Used when the student opens a topic the engine's cursor is not on: they
    picked a different lesson from the list, or they finished the course and
    came back to it. Without this the state keeps a cursor belonging somewhere
    else -- or nowhere at all, once ``completed`` -- ``_current_step_payload``
    returns ``None``, and the player silently drops out of path mode into a
    flat playlist with no questions after any concept.
    """
    mode, step, question = _enter_topic(node)
    if question is None:
        return False

    learning_state.current_lesson_node = node
    learning_state.current_module = top_level_ancestor(node)
    learning_state.current_variant = "normal"
    learning_state.current_chunk_id = None
    learning_state.current_question_attempts = 0
    learning_state.remediation_stack = []
    learning_state.remediated_positions = []
    learning_state.completed = False
    if fresh_attempt:
        # Answers from the finished run stay on record but stop counting as
        # "already cleared" -- see LearningState.attempt_started_at.
        learning_state.attempt_started_at = timezone.now()

    if mode == "path":
        learning_state.current_step_position = step["position"]
        learning_state.current_generated_question = GeneratedQuestion.objects.get(pk=question["id"])
        learning_state.current_question = None
    else:
        learning_state.current_step_position = None
        learning_state.current_generated_question = None
        learning_state.current_question = question
    return True


def resolve_learning_start(course):
    """Like ``resolve_start``, but tries every topic's published learning
    path before falling back to the flat ``lessons.Question`` walk. This is
    the entry point ``StartLearningView`` uses; ``resolve_start`` stays as
    the plain legacy walk other callers (and its own tests) still rely on.

    Returns ``{"mode": "path", "module", "node", "path", "step", "question"}``,
    ``{"mode": "legacy", "module", "node", "question"}``, or the same legacy
    shape with everything but ``module`` (and ``node`` when a module exists)
    ``None`` if the course has no answerable content anywhere.
    """
    for node in _course_topic_nodes(course):
        mode, step, question = _enter_topic(node)
        if question is None:
            continue
        module = top_level_ancestor(node)
        if mode == "path":
            return {
                "mode": "path",
                "module": module,
                "node": node,
                "path": published_path_for(node),
                "step": step,
                "question": question,
            }
        return {"mode": "legacy", "module": module, "node": node, "question": question}

    module, node, question = resolve_start(course)
    return {"mode": "legacy", "module": module, "node": node, "question": question}


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------

def _update_concept_mastery(learning_state, key, is_correct):
    """Apply the BKT update to one concept's own mastery. Returns ``(before, after)``.

    Runs beside, not instead of, the course-wide update on
    ``learning_state.mastery``; see ``LearningState.concept_mastery``.
    """
    before = learning_state.concept_mastery.get(key)
    if before is None:
        before = AdaptiveConfig.load().starting_mastery
    after = _bkt_update(before, is_correct)
    # A new dict rather than an in-place write, so the change is unmistakable.
    learning_state.concept_mastery = {**learning_state.concept_mastery, key: after}
    return before, after


def _bkt_update(prior_mastery, is_correct):
    config = AdaptiveConfig.load()
    if is_correct:
        numerator = prior_mastery * (1 - config.p_slip)
        denom = numerator + (1 - prior_mastery) * config.p_guess
    else:
        numerator = prior_mastery * config.p_slip
        denom = numerator + (1 - prior_mastery) * (1 - config.p_guess)
    posterior = numerator / max(denom, 1e-6)
    new_mastery = posterior + (1 - posterior) * config.p_learn
    return max(0.0, min(config.mastery_ceiling, new_mastery))


class AdaptiveEngine:
    @staticmethod
    def evaluate(learning_state, question, selected_answer):
        course = learning_state.course
        is_correct = answer_is_correct(question, selected_answer)

        learning_state.mastery = _bkt_update(learning_state.mastery, is_correct)
        concept_key = f"topic:{learning_state.current_lesson_node_id}"
        mastery_before, mastery_after = _update_concept_mastery(learning_state, concept_key, is_correct)
        learning_state.attempts += 1

        advance = is_correct
        if not is_correct:
            learning_state.current_question_attempts += 1
            if learning_state.current_question_attempts >= MAX_QUESTION_ATTEMPTS:
                advance = True

        if advance:
            next_module, next_node, next_question = _next_step(
                course, learning_state.current_lesson_node, question
            )
            learning_state.current_question_attempts = 0
            if next_question is None:
                learning_state.completed = True
                learning_state.current_question = None
                action = StudentResponse.Action.COMPLETE
            else:
                learning_state.current_module = next_module
                learning_state.current_lesson_node = next_node
                learning_state.current_question = next_question
                action = StudentResponse.Action.ADVANCE
        else:
            action = StudentResponse.Action.RETRY

        learning_state.save()

        return {
            "is_correct": is_correct,
            "mastery": learning_state.mastery,
            "completed": learning_state.completed,
            "next_module": learning_state.current_module_id,
            "next_lesson_node": learning_state.current_lesson_node_id,
            "next_question": learning_state.current_question_id,
            # Decision log -- recorded on StudentResponse, not sent to devices.
            "action": action,
            "concept_key": concept_key,
            "concept_mastery_before": mastery_before,
            "concept_mastery_after": mastery_after,
        }

    # -----------------------------------------------------------------------
    # path mode -- see the module docstring and learning_path/HANDOFF.md § 4
    # -----------------------------------------------------------------------

    @staticmethod
    def evaluate_path(learning_state, path, question, selected_answer):
        """Path-mode counterpart to ``evaluate``.

        ``path`` is the topic's published learning path (``published_path_for``);
        ``question`` is one entry of the *current step's* ``questions`` list --
        a dict, not a ``GeneratedQuestion`` row -- for whichever chunk
        (``learning_state.current_chunk``) is currently active.

        On a miss, this reacts every time rather than batching three identical
        misses before doing anything: the content variant escalates in place
        first (normal -> simplified -> elaborated, one rung per miss, same
        question shown again each time) before anything structural happens.
        Only once elaborated has also failed does it reroute -- first through
        the step's nearest prerequisite (HANDOFF.md's own sketch), then this
        step's *alternate* chunk (a different uploaded PDF's own telling of
        the same concept, rather than another reword of the same one), and
        for a step that had neither, one more pass through the ladder before
        giving up. See ``_reroute`` and ``adaptive/PATH_MODE.md``.
        """
        is_correct = path_answer_is_correct(question, selected_answer)
        current_step = _step_by_position(path, learning_state.current_step_position)
        learning_state.mastery = _bkt_update(learning_state.mastery, is_correct)
        concept_key = f"concept:{current_step['concept_id']}"
        mastery_before, mastery_after = _update_concept_mastery(learning_state, concept_key, is_correct)
        learning_state.attempts += 1

        if is_correct:
            learning_state.current_question_attempts = 0
            chunk_id = learning_state.current_chunk_id
            answered = _step_answered_ids(learning_state, current_step, chunk_id) | {question["id"]}
            next_question = _next_question_in_step(current_step, answered, chunk_id)

            if next_question is not None:
                learning_state.current_generated_question_id = next_question["id"]
                action = StudentResponse.Action.NEXT_QUESTION
            else:
                action = AdaptiveEngine._resume_or_advance(
                    learning_state, path, learning_state.current_step_position
                )
        else:
            learning_state.current_question_attempts += 1
            if learning_state.current_variant != "elaborated":
                # Cheapest remedy first: re-explain the same question's
                # content one rung plainer/richer before anything structural.
                learning_state.current_variant = _escalate_variant(learning_state.current_variant)
                action = StudentResponse.Action.ESCALATE_VARIANT
            else:
                action = AdaptiveEngine._reroute(learning_state, path, current_step)

        learning_state.save()

        return {
            "is_correct": is_correct,
            "mastery": learning_state.mastery,
            "completed": learning_state.completed,
            "next_module": learning_state.current_module_id,
            "next_lesson_node": learning_state.current_lesson_node_id,
            "next_step_position": learning_state.current_step_position,
            "remediation_target_position": (
                learning_state.remediation_stack[-1]["position"]
                if learning_state.remediation_stack else None
            ),
            "current_chunk": learning_state.current_chunk_id,
            "current_variant": learning_state.current_variant,
            # Usually the path-mode question just assigned; but _advance_past
            # can hand off to a topic with no published path (legacy mode),
            # where the assignment lands on current_question instead -- still
            # "the next question to answer" from the caller's point of view.
            "next_question": (
                learning_state.current_generated_question_id
                or learning_state.current_question_id
            ),
            # Decision log -- recorded on StudentResponse, not sent to devices.
            "action": action,
            "concept_key": concept_key,
            "concept_mastery_before": mastery_before,
            "concept_mastery_after": mastery_after,
        }

    @staticmethod
    def _reroute(learning_state, path, current_step):
        """``current_step`` has just failed at its most elaborated variant --
        nowhere left to escalate *this* question's content to. Tries, in
        priority order:

        1. Detour through the nearest prerequisite (last in ``prerequisites``,
           already nearest-last in path order), if one exists and the
           remediation stack has room (``MAX_REMEDIATION_DEPTH``) -- the
           bigger, structural intervention, tried first per the user's own
           ruling. A prerequisite step is, by definition, one the learner
           already cleared to get here; its first question is re-served as a
           refresher regardless of that history.
        2. This step's alternate chunk, if it has one and hasn't been tried
           yet -- a different PDF's independent explanation, tried only once
           a detour isn't possible (a leaf concept with no prerequisite) or
           the remediation stack is already full.
        3. A second pass through the variant ladder, but *only* for a step
           that had neither of the above available -- no prerequisite and no
           alternate, so nothing structural could ever help it. Every other
           step gets the ladder plus one structural intervention; a root
           concept would get the ladder alone and then be pushed into
           material that depends on the concept it just missed. Bounded by
           ``MAX_LADDER_PASSES``.
        4. Nothing left to try: give up rerouting this step and move the
           learner on, via ``_resume_or_advance`` -- the same "never strand
           anyone" floor the rest of this engine already guarantees. Crucial
           that this goes through ``_resume_or_advance`` and not straight to
           ``_advance_past``: if this step was itself a prerequisite detour,
           there may still be an outer step waiting on the remediation stack
           to be resumed.
        """
        prerequisites = current_step["prerequisites"]
        position = learning_state.current_step_position
        # A step gets one detour, not one per failure. The stack is popped on
        # the way back (see _resume_or_advance), so without this record a step
        # that fails again immediately after being resumed would detour into
        # the same prerequisite again, and again -- a learner who keeps missing
        # never escapes the loop. MAX_REMEDIATION_DEPTH bounds nesting; this
        # bounds repetition.
        spent = position in learning_state.remediated_positions
        if prerequisites and not spent and len(learning_state.remediation_stack) < MAX_REMEDIATION_DEPTH:
            target_step = _step_by_position(path, _position_of_concept(path, prerequisites[-1]))
            target_question = _next_question_in_step(target_step, exclude_ids=())
            if target_question is not None:
                learning_state.remediation_stack = learning_state.remediation_stack + [{
                    "position": position,
                    "chunk_id": learning_state.current_chunk_id,
                }]
                learning_state.remediated_positions = learning_state.remediated_positions + [position]
                learning_state.current_step_position = target_step["position"]
                learning_state.current_chunk_id = None
                learning_state.current_variant = "normal"
                learning_state.current_question_attempts = 0
                learning_state.current_generated_question_id = target_question["id"]
                return StudentResponse.Action.DETOUR_PREREQUISITE

        alternates = current_step.get("alternates") or []
        alternate = _unused_alternate(current_step, learning_state.current_chunk_id)
        if alternate is not None:
            alt_question = _next_question_in_step(
                current_step, exclude_ids=(), chunk_id=alternate["learning_object_id"]
            )
            if alt_question is not None:
                learning_state.current_chunk_id = alternate["learning_object_id"]
                learning_state.current_variant = "normal"
                learning_state.current_question_attempts = 0
                learning_state.current_generated_question_id = alt_question["id"]
                return StudentResponse.Action.SWITCH_SOURCE

        # Nothing structural was ever available for this step: no prerequisite
        # to send the learner back through, and no second book's telling of the
        # concept to switch to. Every other step gets the ladder *plus* one
        # structural intervention; this one would get the ladder alone and then
        # be pushed forward into material that depends on the very concept it
        # just missed. Teach it once more from the top instead, so the effort
        # spent on it is comparable. Bounded by MAX_LADDER_PASSES.
        if not prerequisites and not alternates:
            if learning_state.current_question_attempts < MAX_LADDER_PASSES * MAX_STEP_QUESTION_ATTEMPTS:
                # Deliberately *not* resetting current_question_attempts: it is
                # what counts the passes.
                learning_state.current_variant = "normal"
                return StudentResponse.Action.SECOND_PASS

        learning_state.current_question_attempts = 0
        return AdaptiveEngine._resume_or_advance(learning_state, path, learning_state.current_step_position)

    @staticmethod
    def _resume_or_advance(learning_state, path, position):
        """Step ``position`` has nothing left to do on it -- either its
        questions are all answered, or ``_reroute`` gave up trying to
        remediate it. Resume whatever's waiting on the remediation stack
        (possibly several detours deep), preferring an unused alternate chunk
        over repeating content that already failed; if nothing is pending,
        move on to the next step or topic.
        """
        if learning_state.remediation_stack:
            frame = learning_state.remediation_stack[-1]
            learning_state.remediation_stack = learning_state.remediation_stack[:-1]
            resume_step = _step_by_position(path, frame["position"])
            resume_chunk_id = frame["chunk_id"]

            # A detour only ever triggers once a step has failed at
            # "elaborated" (see evaluate_path), so whichever chunk was active
            # when we left was necessarily showing its most elaborated
            # version. Prefer a chunk this step hasn't shown yet; only if
            # there isn't one do we pick back up at that same elaborated
            # variant instead of re-walking normal -> simplified on content
            # that's already known not to have worked.
            alternate = _unused_alternate(resume_step, resume_chunk_id)
            if alternate is not None:
                resume_chunk_id = alternate["learning_object_id"]
                resume_variant = "normal"
            else:
                resume_variant = "elaborated"

            # Ignore StudentResponse history here, the same way the initial
            # detour-in does (`_reroute`'s prerequisite branch always calls
            # _next_question_in_step with exclude_ids=()): a step on the
            # remediation stack is, by path-mode's own invariant, one the
            # learner already answered correctly *before* ever needing
            # remediation -- that's how they reached whatever depends on it.
            # Checking history here would find that old record on the very
            # first pop and wrongly call the refresher done before it was
            # ever re-served this time.
            resume_question = _next_question_in_step(resume_step, exclude_ids=(), chunk_id=resume_chunk_id)
            if resume_question is not None:
                learning_state.current_step_position = frame["position"]
                learning_state.current_chunk_id = resume_chunk_id
                learning_state.current_variant = resume_variant
                learning_state.current_question_attempts = 0
                learning_state.current_generated_question_id = resume_question["id"]
                return StudentResponse.Action.RESUME
            # The struggled-on step is fully cleared too (e.g. from an
            # earlier pass) -- nothing left to resume there; keep unwinding.
            return AdaptiveEngine._resume_or_advance(learning_state, path, frame["position"])

        return AdaptiveEngine._advance_past(learning_state, path, position)

    @staticmethod
    def _advance_past(learning_state, path, position):
        """Move on from a fully-settled step at ``position`` (nothing left to
        resume for it either): the next step in this topic's path, else the
        next topic in the course (path or legacy mode, whichever it has),
        else course completion. Always starts fresh at a step's representative
        chunk -- chunk-switching is a within-step remediation device, not
        something a new step inherits."""
        next_step = _next_step_with_question(path, position, learning_state)
        if next_step is not None:
            learning_state.current_step_position = next_step["position"]
            learning_state.current_chunk_id = None
            learning_state.current_variant = "normal"
            learning_state.current_question_attempts = 0
            question = _next_question_in_step(next_step, _step_answered_ids(learning_state, next_step))
            learning_state.current_generated_question = GeneratedQuestion.objects.get(pk=question["id"])
            return StudentResponse.Action.ADVANCE

        next_node = _next_topic_with_content(learning_state.course, learning_state.current_lesson_node)
        if next_node is None:
            learning_state.completed = True
            learning_state.current_generated_question = None
            learning_state.current_question = None
            learning_state.current_step_position = None
            learning_state.current_chunk_id = None
            learning_state.remediation_stack = []
            learning_state.remediated_positions = []
            return StudentResponse.Action.COMPLETE

        mode, next_step, next_question = _enter_topic(next_node)
        learning_state.current_lesson_node = next_node
        learning_state.current_module = top_level_ancestor(next_node)
        learning_state.current_variant = "normal"
        learning_state.current_chunk_id = None
        learning_state.current_question_attempts = 0
        learning_state.remediation_stack = []
        learning_state.remediated_positions = []
        if mode == "path":
            learning_state.current_step_position = next_step["position"]
            learning_state.current_generated_question = GeneratedQuestion.objects.get(pk=next_question["id"])
            learning_state.current_question = None
        else:
            learning_state.current_step_position = None
            learning_state.current_generated_question = None
            learning_state.current_question = next_question
        return StudentResponse.Action.ADVANCE
