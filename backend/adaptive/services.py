from lessons.models import LearningObject
from course.models import CourseModule, LessonNode
from question_generation.models import GeneratedQuestion

from .models import StudentResponse


P_GUESS = 0.20
P_SLIP = 0.10
P_LEARN = 0.15
MASTERY_CEILING = 0.99

TIER_BUCKETS = GeneratedQuestion.TIER_BUCKETS
DIFFICULTY_ORDER = [level for level, _ in GeneratedQuestion.DIFFICULTY_CHOICES]
VARIANT_LEVELS = ["NORMAL", "ELABORATED", "SIMPLIFIED"]

STARTING_MASTERY = 0.30
STARTING_BLOOM = TIER_BUCKETS[0][0]
STARTING_VARIANT = "NORMAL"
DEFAULT_DIFFICULTY = "medium"


def _bucket_index(bloom_level):
    for index, bucket in enumerate(TIER_BUCKETS):
        if bloom_level in bucket:
            return index
    return 0


def _step_down_difficulty(difficulty):
    if difficulty not in DIFFICULTY_ORDER:
        return DEFAULT_DIFFICULTY
    index = DIFFICULTY_ORDER.index(difficulty)
    return DIFFICULTY_ORDER[max(index - 1, 0)]


def _variant_index(variant_name):
    return VARIANT_LEVELS.index(variant_name)


def _variant_name(index):
    index = max(0, min(index, len(VARIANT_LEVELS) - 1))
    return VARIANT_LEVELS[index]


def _text_chunks(lesson_node):
    return list(
        lesson_node.learning_objects.filter(kind=LearningObject.Kind.TEXT).order_by("order", "id")
    )


def _first_chunk(lesson_node):
    chunks = _text_chunks(lesson_node)
    return chunks[0] if chunks else None


def _next_chunk(lesson_node, current_chunk):
    chunks = _text_chunks(lesson_node)
    ids = [chunk.id for chunk in chunks]
    try:
        index = ids.index(current_chunk.id)
    except ValueError:
        return None
    return chunks[index + 1] if index + 1 < len(chunks) else None


def _next_lesson_node(current_node):
    next_node = (
        LessonNode.objects.filter(
            module=current_node.module,
            source__order__gt=current_node.source.order,
        )
        .order_by("source__order", "source__id")
        .first()
    )
    if next_node is not None:
        return next_node

    next_module = (
        CourseModule.objects.filter(
            is_active=True,
            source__course=current_node.module.source.course,
            source__order__gt=current_node.module.source.order,
        )
        .order_by("source__order", "source__id")
        .first()
    )
    if next_module is None:
        return None

    return next_module.lesson_nodes.order_by(
        "source__depth", "source__order", "source__id"
    ).first()


def _attempted_question_ids(learning_state, chunk, bucket_levels):
    return list(
        StudentResponse.objects.filter(
            learning_state=learning_state,
            question__node=chunk,
            question__bloom_level__in=bucket_levels,
        ).values_list("question_id", flat=True)
    )


def _pick_question(chunk, bucket_levels, difficulty, exclude_ids):
    """Pick a question for (chunk, bloom bucket), preferring `difficulty` and
    one not already attempted — the remediation pool for a wrong answer."""
    pool = GeneratedQuestion.objects.filter(node=chunk, bloom_level__in=bucket_levels)

    question = pool.filter(difficulty=difficulty).exclude(id__in=exclude_ids).order_by("id").first()
    if question:
        return question

    question = pool.exclude(id__in=exclude_ids).order_by("id").first()
    if question:
        return question

    # pool exhausted — every question in this bucket has already been seen
    return pool.order_by("id").first()


def resolve_start(lesson_node):
    """(chunk, question) to serve first for a lesson node, or (None, None)
    if it has no text chunks / no generated questions yet."""
    chunk = _first_chunk(lesson_node)
    if chunk is None:
        return None, None
    return chunk, _pick_question(chunk, TIER_BUCKETS[0], DEFAULT_DIFFICULTY, [])


def _choice_label_for_answer(question):
    answer = (question.correct_answer or "").strip()
    if answer.upper() in {"A", "B", "C", "D"}:
        return answer.upper()

    choices = question.choices or []
    for index, choice in enumerate(choices[:4]):
        if str(choice).strip().lower() == answer.lower():
            return "ABCD"[index]
    return answer.upper()


class AdaptiveScoringService:
    @staticmethod
    def evaluate(learning_state, question, selected_answer, response_time):
        is_correct = selected_answer.strip().upper() == _choice_label_for_answer(question)

        prior_mastery = learning_state.mastery
        if is_correct:
            numerator = prior_mastery * (1 - P_SLIP)
            denom = numerator + (1 - prior_mastery) * P_GUESS
        else:
            numerator = prior_mastery * P_SLIP
            denom = numerator + (1 - prior_mastery) * (1 - P_GUESS)
        posterior = numerator / max(denom, 1e-6)
        new_mastery = posterior + (1 - posterior) * P_LEARN
        new_mastery = max(0.0, min(MASTERY_CEILING, new_mastery))

        current_chunk = question.node
        current_node = learning_state.current_node
        bucket_index = _bucket_index(question.bloom_level)

        next_node = current_node
        next_chunk = current_chunk
        node_changed = False
        chunk_changed = False
        completed = False
        tier_reset = True

        if is_correct:
            next_variant = STARTING_VARIANT

            if bucket_index < len(TIER_BUCKETS) - 1:
                # one tier down, three to go — same chunk, next bloom tier
                next_bucket = TIER_BUCKETS[bucket_index + 1]
                exclude_ids = _attempted_question_ids(learning_state, current_chunk, next_bucket)
                next_question = _pick_question(current_chunk, next_bucket, DEFAULT_DIFFICULTY, exclude_ids)
            else:
                candidate_chunk = _next_chunk(current_node, current_chunk)
                if candidate_chunk is not None:
                    # chunk's three tiers cleared — next topic, same lesson
                    next_chunk = candidate_chunk
                    chunk_changed = True
                    next_question = _pick_question(next_chunk, TIER_BUCKETS[0], DEFAULT_DIFFICULTY, [])
                else:
                    candidate_node = _next_lesson_node(current_node)
                    if candidate_node is not None:
                        # lesson's chunks cleared — next lesson
                        next_node = candidate_node
                        node_changed = True
                        next_chunk, next_question = resolve_start(next_node)
                        new_mastery = STARTING_MASTERY
                    else:
                        completed = True
                        next_question = None
        else:
            # wrong: same chunk, same bloom tier — remediate with a different,
            # easier question from the pool; escalate the narration variant.
            next_difficulty = _step_down_difficulty(question.difficulty)
            bucket_levels = TIER_BUCKETS[bucket_index]
            # The StudentResponse row for *this* attempt isn't saved until
            # after evaluate() returns, so the question just missed wouldn't
            # otherwise be in its own exclusion set — add it explicitly, or
            # remediation can hand the learner back the very question they
            # just got wrong.
            exclude_ids = _attempted_question_ids(learning_state, current_chunk, bucket_levels)
            exclude_ids.append(question.id)
            next_question = _pick_question(current_chunk, bucket_levels, next_difficulty, exclude_ids)

            prior_variant_index = _variant_index(learning_state.current_variant)
            next_variant = _variant_name(prior_variant_index + 1)
            tier_reset = False

        next_bloom = next_question.bloom_level if next_question else learning_state.current_bloom
        reward = 1.0 if is_correct else -1.0

        learning_state.mastery = new_mastery
        learning_state.current_variant = next_variant
        learning_state.current_bloom = next_bloom
        learning_state.current_question = next_question
        learning_state.current_node = next_node
        learning_state.current_module = next_node.module
        learning_state.attempts += 1
        learning_state.tier_attempts = 0 if tier_reset else learning_state.tier_attempts + 1
        learning_state.reward = reward
        learning_state.completed = completed
        learning_state.save()

        return {
            "is_correct": is_correct,
            "mastery": new_mastery,
            "reward": reward,
            "next_node": next_node.id,
            "node_changed": node_changed,
            "next_chunk": next_chunk.id if next_chunk else None,
            "chunk_changed": chunk_changed,
            "next_variant": next_variant,
            "next_bloom": next_bloom,
            "next_question": next_question.id if next_question else None,
            "completed": completed,
        }
