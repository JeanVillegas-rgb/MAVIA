from course.models import CourseModule, LessonNode, ModuleQuestion


ESCALATE_THRESHOLD = 0.70
DEESCALATE_THRESHOLD = 0.40
NODE_MASTERED_THRESHOLD = 0.90

P_GUESS = 0.20
P_SLIP = 0.10
P_LEARN = 0.15
MASTERY_CEILING = 0.99

BLOOM_TIERS = ["REMEMBER", "UNDERSTAND", "ANALYZE"]
VARIANT_LEVELS = ["NORMAL", "ELABORATED", "SIMPLIFIED"]

STARTING_MASTERY = 0.30
STARTING_BLOOM = "REMEMBER"
STARTING_VARIANT = "NORMAL"


def _tier_index(bloom_level):
    return BLOOM_TIERS.index(bloom_level)


def _tier_name(index):
    index = max(0, min(index, len(BLOOM_TIERS) - 1))
    return BLOOM_TIERS[index]


def _variant_index(variant_name):
    return VARIANT_LEVELS.index(variant_name)


def _variant_name(index):
    index = max(0, min(index, len(VARIANT_LEVELS) - 1))
    return VARIANT_LEVELS[index]


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
        prior_tier_index = _tier_index(learning_state.current_bloom)
        prior_variant_index = _variant_index(learning_state.current_variant)

        if is_correct:
            numerator = prior_mastery * (1 - P_SLIP)
            denom = numerator + (1 - prior_mastery) * P_GUESS
        else:
            numerator = prior_mastery * P_SLIP
            denom = numerator + (1 - prior_mastery) * (1 - P_GUESS)

        posterior = numerator / max(denom, 1e-6)
        new_mastery = posterior + (1 - posterior) * P_LEARN
        new_mastery = max(0.0, min(MASTERY_CEILING, new_mastery))

        questions_in_tier = ModuleQuestion.objects.filter(
            lesson_node=learning_state.current_node,
            bloom_level=learning_state.current_bloom,
        ).count()
        tier_attempts_used = learning_state.tier_attempts + 1
        tier_fully_attempted = tier_attempts_used >= max(questions_in_tier, 1)

        if new_mastery > ESCALATE_THRESHOLD and is_correct and tier_fully_attempted:
            new_tier_index = min(prior_tier_index + 1, len(BLOOM_TIERS) - 1)
        elif new_mastery < DEESCALATE_THRESHOLD:
            new_tier_index = max(prior_tier_index - 1, 0)
        else:
            new_tier_index = prior_tier_index

        next_bloom = _tier_name(new_tier_index)
        node_mastered_now = (
            is_correct
            and prior_tier_index == len(BLOOM_TIERS) - 1
            and new_mastery >= NODE_MASTERED_THRESHOLD
            and tier_fully_attempted
        )

        if node_mastered_now:
            reward = 1.0
        elif new_tier_index > prior_tier_index:
            reward = 1.0
        elif new_tier_index < prior_tier_index:
            reward = -1.0
        else:
            reward = 0.0

        if not is_correct:
            next_variant = _variant_name(
                min(prior_variant_index + 1, len(VARIANT_LEVELS) - 1)
            )
        else:
            next_variant = STARTING_VARIANT

        current_node = learning_state.current_node
        next_node = current_node
        completed = False

        if node_mastered_now:
            next_node = (
                LessonNode.objects.filter(
                    module=current_node.module,
                    source__order__gt=current_node.source.order,
                )
                .order_by("source__order", "source__id")
                .first()
            )
            if next_node is None:
                next_module = (
                    CourseModule.objects.filter(
                        is_active=True,
                        source__course=current_node.module.source.course,
                        source__order__gt=current_node.module.source.order,
                    )
                    .order_by("source__order", "source__id")
                    .first()
                )
                if next_module is not None:
                    next_node = next_module.lesson_nodes.order_by(
                        "source__depth", "source__order", "source__id"
                    ).first()

            if next_node is None:
                next_node = current_node
                completed = True
            else:
                new_mastery = STARTING_MASTERY
                next_bloom = STARTING_BLOOM
                next_variant = STARTING_VARIANT

        tier_or_node_changed = new_tier_index != prior_tier_index or node_mastered_now
        learning_state.mastery = new_mastery
        learning_state.current_variant = next_variant
        learning_state.current_bloom = next_bloom
        learning_state.current_node = next_node
        learning_state.current_module = next_node.module
        learning_state.attempts += 1
        learning_state.tier_attempts = 0 if tier_or_node_changed else tier_attempts_used
        learning_state.reward = reward
        learning_state.completed = completed
        learning_state.save()

        return {
            "is_correct": is_correct,
            "mastery": new_mastery,
            "reward": reward,
            "next_node": next_node.id,
            "node_changed": next_node.id != current_node.id,
            "next_variant": next_variant,
            "next_bloom": next_bloom,
            "completed": completed,
        }
