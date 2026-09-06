"""Adaptive progression engine.

Adapter port of ``Milestone1-Jean/backend/adaptive/services.py``. The BKT
mastery update is kept verbatim; the Bloom-tier question selection is replaced
with a plain linear walk over ``lessons.Question.order`` because mavia's
question model carries no Bloom / difficulty axis.
"""

from lessons.models import OutlineNode, Question

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
        OutlineNode.objects.filter(course=course, parent__isnull=False)
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
# engine
# ---------------------------------------------------------------------------

def _bkt_update(prior_mastery, is_correct):
    if is_correct:
        numerator = prior_mastery * (1 - P_SLIP)
        denom = numerator + (1 - prior_mastery) * P_GUESS
    else:
        numerator = prior_mastery * P_SLIP
        denom = numerator + (1 - prior_mastery) * (1 - P_GUESS)
    posterior = numerator / max(denom, 1e-6)
    new_mastery = posterior + (1 - posterior) * P_LEARN
    return max(0.0, min(MASTERY_CEILING, new_mastery))


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
