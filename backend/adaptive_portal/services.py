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

from lessons.models import OutlineNode, Question
from adaptive_config.models import AdaptiveConfig
from learning_path.services import get_published_path
from question_generation.models import GeneratedQuestion

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

# Same cap as MAX_QUESTION_ATTEMPTS: after this many misses on one question,
# the engine stops asking it again -- either by detouring through a
# prerequisite, or (if there is none, or the detour is already underway) by
# re-teaching the same step in a plainer variant.
MAX_STEP_QUESTION_ATTEMPTS = 3

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


def _ordered_step_questions(step):
    """LOT before HOT, then id -- a step holds one concept's questions, so
    this plays the role TIER_BUCKETS plays for the legacy engine without
    needing bucket machinery."""
    return sorted(step["questions"], key=lambda q: (q["thinking_order"] == "HOT", q["id"]))


def _next_question_in_step(step, exclude_ids):
    for question in _ordered_step_questions(step):
        if question["id"] not in exclude_ids:
            return question
    return None


def _next_step_with_question(path, after_position):
    for step in path["steps"]:
        if step["position"] > after_position and _ordered_step_questions(step):
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


def path_answer_is_correct(question, selected_answer):
    """``question`` is one entry of a step's ``questions`` list (a dict from
    ``get_published_path``, not a ``GeneratedQuestion`` row)."""
    correct = str(question["correct_answer"] or "").strip()
    selected = str(selected_answer or "").strip()
    if selected.upper() == correct.upper():
        return True
    choices = question.get("choices") or {}
    if isinstance(choices, dict):
        for letter, text in choices.items():
            if letter.upper() == correct.upper() and str(text).strip().lower() == selected.lower():
                return True
    return False


def _step_answered_ids(learning_state, step):
    """Ids of this step's questions the learner already has correct on
    record -- used so re-entering a step (e.g. resuming after a prerequisite
    detour) does not re-ask something already cleared."""
    step_ids = {q["id"] for q in step["questions"]}
    return set(
        learning_state.responses.filter(
            generated_question_id__in=step_ids, is_correct=True
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
    return {**step, "questions": [student_safe_question(q) for q in step["questions"]]}


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
            else:
                learning_state.current_module = next_module
                learning_state.current_lesson_node = next_node
                learning_state.current_question = next_question

        learning_state.save()

        return {
            "is_correct": is_correct,
            "mastery": learning_state.mastery,
            "completed": learning_state.completed,
            "next_module": learning_state.current_module_id,
            "next_lesson_node": learning_state.current_lesson_node_id,
            "next_question": learning_state.current_question_id,
        }

    # -----------------------------------------------------------------------
    # path mode -- see the module docstring and learning_path/HANDOFF.md § 4
    # -----------------------------------------------------------------------

    @staticmethod
    def evaluate_path(learning_state, path, question, selected_answer):
        """Path-mode counterpart to ``evaluate``.

        ``path`` is the topic's published learning path (``published_path_for``);
        ``question`` is one entry of the *current step's* ``questions`` list --
        a dict, not a ``GeneratedQuestion`` row. On repeated failure this sends
        the learner through the step's nearest prerequisite (HANDOFF.md's own
        sketch) instead of just stepping down difficulty in place; a step with
        no prerequisite to fall back on is re-taught in a plainer variant
        instead, mirroring the legacy engine's step-down-and-retry.
        """
        is_correct = path_answer_is_correct(question, selected_answer)
        learning_state.mastery = _bkt_update(learning_state.mastery, is_correct)
        learning_state.attempts += 1
        current_step = _step_by_position(path, learning_state.current_step_position)

        if is_correct:
            learning_state.current_question_attempts = 0
            answered = _step_answered_ids(learning_state, current_step) | {question["id"]}
            next_question = _next_question_in_step(current_step, answered)

            if next_question is not None:
                learning_state.current_generated_question = GeneratedQuestion.objects.get(
                    pk=next_question["id"]
                )
            elif learning_state.remediation_target_position is not None:
                # Cleared the prerequisite detour -- resume where the learner
                # struggled, picking up whichever of its questions aren't
                # already answered.
                resume_position = learning_state.remediation_target_position
                learning_state.remediation_target_position = None
                resume_step = _step_by_position(path, resume_position)
                resume_question = _next_question_in_step(
                    resume_step, _step_answered_ids(learning_state, resume_step)
                )
                if resume_question is not None:
                    learning_state.current_step_position = resume_position
                    learning_state.current_generated_question = GeneratedQuestion.objects.get(
                        pk=resume_question["id"]
                    )
                else:
                    # The struggled-on step is now fully cleared too --
                    # nothing left to resume, carry straight on from there.
                    AdaptiveEngine._advance_past(learning_state, path, resume_position)
            else:
                AdaptiveEngine._advance_past(learning_state, path, learning_state.current_step_position)
        else:
            learning_state.current_question_attempts += 1
            if learning_state.current_question_attempts >= MAX_STEP_QUESTION_ATTEMPTS:
                learning_state.current_question_attempts = 0
                prerequisites = current_step["prerequisites"]
                detoured = False
                if learning_state.remediation_target_position is None and prerequisites:
                    # Already in path order, nearest (highest position) last.
                    target_step = _step_by_position(path, _position_of_concept(path, prerequisites[-1]))
                    # A prerequisite step is, by definition, one the learner
                    # already cleared to get here -- re-serve its first
                    # question as a refresher regardless of that history,
                    # rather than only when it happens to have one left over.
                    target_question = _next_question_in_step(target_step, exclude_ids=())
                    if target_question is not None:
                        learning_state.remediation_target_position = learning_state.current_step_position
                        learning_state.current_step_position = target_step["position"]
                        learning_state.current_generated_question = GeneratedQuestion.objects.get(
                            pk=target_question["id"]
                        )
                        learning_state.current_variant = "normal"
                        detoured = True
                if not detoured:
                    # No prerequisite to send them through (or the detour
                    # itself is what's failing) -- re-teach this step plainer
                    # rather than recurse into a second level of remediation.
                    learning_state.current_variant = _escalate_variant(learning_state.current_variant)

        learning_state.save()

        return {
            "is_correct": is_correct,
            "mastery": learning_state.mastery,
            "completed": learning_state.completed,
            "next_module": learning_state.current_module_id,
            "next_lesson_node": learning_state.current_lesson_node_id,
            "next_step_position": learning_state.current_step_position,
            "remediation_target_position": learning_state.remediation_target_position,
            "current_variant": learning_state.current_variant,
            "next_question": learning_state.current_generated_question_id,
        }

    @staticmethod
    def _advance_past(learning_state, path, position):
        """Move on from a fully-cleared step at ``position``: the next step
        in this topic's path, else the next topic in the course (path or
        legacy mode, whichever it has), else course completion."""
        next_step = _next_step_with_question(path, position)
        if next_step is not None:
            learning_state.current_step_position = next_step["position"]
            learning_state.current_variant = "normal"
            question = _next_question_in_step(next_step, _step_answered_ids(learning_state, next_step))
            learning_state.current_generated_question = GeneratedQuestion.objects.get(pk=question["id"])
            return learning_state

        next_node = _next_topic_with_content(learning_state.course, learning_state.current_lesson_node)
        if next_node is None:
            learning_state.completed = True
            learning_state.current_generated_question = None
            learning_state.current_question = None
            learning_state.current_step_position = None
            return learning_state

        mode, next_step, next_question = _enter_topic(next_node)
        learning_state.current_lesson_node = next_node
        learning_state.current_module = top_level_ancestor(next_node)
        learning_state.current_variant = "normal"
        if mode == "path":
            learning_state.current_step_position = next_step["position"]
            learning_state.current_generated_question = GeneratedQuestion.objects.get(pk=next_question["id"])
            learning_state.current_question = None
        else:
            learning_state.current_step_position = None
            learning_state.current_generated_question = None
            learning_state.current_question = next_question
        return learning_state
