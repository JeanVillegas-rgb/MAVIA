"""The question bank's own rules: when a question is final, which ones fill a concept's minimum, and the rows the Questions screen reads."""

import logging

from django.db.models import Q
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from lessons.models import ExtractedQuestion, LearningObject
from lessons.services.question_workflow import question_fingerprint, validation_issues
from question_generation.models import Question

logger = logging.getLogger(__name__)

TIERS = ("LOT", "HOT")
FAILED_CHECKS = ("contradicted", "unsupported")
NOT_CHECKED = "The answer could not be checked right now. Edit and save the question again to retry."
NO_CONCEPT = "No concept's text supports this answer. Choose a concept or revise the question."
CONFIRMED_OBJECTS = {"material__generated_json__learning_objects_confirmed": True}

TYPE_OF_FORMAT = {
    "MCQ": ExtractedQuestion.Type.MULTIPLE_CHOICE,
    "TF": ExtractedQuestion.Type.TRUE_FALSE,
}
FORMAT_OF_TYPE = {question_type: fmt for fmt, question_type in TYPE_OF_FORMAT.items()}
SCREEN_SOURCE = {"generated": "generated", "lesson_pdf": "pdf"}


def bank_values(question_type, choices, correct_answer):
    """The bank's shape of a question: lettered choices and, for multiple choice, the answer's letter."""
    if question_type == ExtractedQuestion.Type.TRUE_FALSE:
        return {
            "question_format": "TF", "choices": None,
            "correct_answer": str(correct_answer or "").capitalize(),
        }
    lettered = {chr(65 + index): choice for index, choice in enumerate(choices or [])}
    letter = next((key for key, choice in lettered.items() if choice == correct_answer), "")
    return {
        "question_format": FORMAT_OF_TYPE.get(question_type, ""),
        "choices": lettered or None,
        "correct_answer": letter,
    }


def screen_values(question):
    """The screen's shape of a bank question: a list of choices and the answer written out."""
    if question.question_format == "TF":
        return {
            "question_type": ExtractedQuestion.Type.TRUE_FALSE,
            "choices": ["True", "False"],
            "correct_answer": question.correct_answer,
        }
    lettered = question.choices if isinstance(question.choices, dict) else {}
    return {
        "question_type": TYPE_OF_FORMAT.get(
            question.question_format, ExtractedQuestion.Type.OPEN_ENDED,
        ),
        "choices": [lettered[key] for key in sorted(lettered)],
        "correct_answer": lettered.get(question.correct_answer, ""),
    }


def structure_issues(question):
    """What the question still lacks before it can be asked: a type, choices or an answer."""
    return validation_issues(**screen_values(question))


def issues(question):
    """What keeps the question from being final, worked out from the question itself."""
    missing = structure_issues(question)
    if missing:
        return missing
    if question.check_result in FAILED_CHECKS and question.node_id:
        from .pipeline import concept_name

        return [f'"{concept_name(question.node)}" does not support this answer: {question.check_note}']
    if question.check_result == "unsupported":
        return [NO_CONCEPT]
    if question.check_result == "unverified" and question.source != "generated":
        return [NOT_CHECKED]
    return []


def settle(question):
    """Set the question's status by the one rule for final, then save it."""
    ready = (
        question.node_id
        and question.paired_by
        and question.thinking_order in TIERS
        and not issues(question)
    )
    question.status = "final" if ready else "review"
    question.content_fingerprint = question_fingerprint(question.question_text)
    question.save()


def fills_minimum(question):
    """A final question fills its concept's minimum; a printed one only once the teacher has edited and paired it."""
    if question.status != "final" or question.thinking_order not in TIERS:
        return False
    if question.source == "lesson_pdf":
        return question.paired_by == "teacher" and question.teacher_edited
    return True


def tier_counts(learning_object):
    """LOTS and HOTS questions filling the minimum of the concept learning_object belongs to."""
    scope = (
        Q(node__group_id=learning_object.group_id)
        if learning_object.group_id
        else Q(node=learning_object)
    )
    questions = Question.objects.filter(
        scope, status="final", **{f"node__{key}": value for key, value in CONFIRMED_OBJECTS.items()},
    )
    counted = [question for question in questions if fills_minimum(question)]
    return {tier: sum(question.thinking_order == tier for question in counted) for tier in TIERS}


def topic_questions(outline_node):
    """Every question of the topic a teacher sees: all but the drafts of a running generation."""
    return (
        Question.objects.filter(
            Q(outline_node=outline_node) | Q(node__material__outline_node=outline_node),
        )
        .exclude(status="draft")
        .select_related("node__material", "node__group")
        .order_by("id")
    )


def duplicate_in_topic(outline_node, fingerprint, exclude_id=None):
    questions = topic_questions(outline_node).filter(content_fingerprint=fingerprint)
    if exclude_id:
        questions = questions.exclude(pk=exclude_id)
    return questions.first()


def _pairing(question):
    """The question's concept in the list form the screen reads, or nothing when it has none."""
    node = question.node
    confirmed = node is not None and (node.material.generated_json or {}).get(
        "learning_objects_confirmed",
    )
    if confirmed:
        review_status = {"auto": "auto_confirmed", "teacher": "teacher_confirmed"}.get(
            question.paired_by, "pending_review",
        )
    elif question.paired_by == "teacher" and node is None:
        review_status = "teacher_unpaired"
    else:
        return []
    return [{
        "learning_object": node.id if confirmed else None,
        "learning_object_group_id": node.group_id if confirmed else None,
        "learning_object_title": node.title if confirmed else "",
        "method": "teacher_selected" if question.paired_by == "teacher" else "answer_check",
        "review_status": review_status,
    }]


def screen_row(question):
    """One bank question as the Questions screen reads it."""
    pairing = _pairing(question)
    problems = issues(question)
    return {
        "id": question.id,
        "outline_node_id": question.outline_node_id,
        "prompt": question.question_text,
        "source_type": SCREEN_SOURCE.get(question.source, "manual"),
        **screen_values(question),
        "bloom_level": question.bloom_level,
        "thinking_order": question.thinking_order,
        "category": question.category,
        "status": question.status,
        "validation_status": "needs_review" if problems else "ready",
        "validation_issues": problems,
        "check_result": question.check_result,
        "check_note": question.check_note,
        "pairing_status": pairing[0]["review_status"] if pairing else "unmatched",
        "learning_object_links": pairing,
    }


def topic_rows(outline_node):
    """(rows, by_group, counted): the topic's questions for the screen, and per concept the paired and the counted ones."""
    rows, by_group, counted = [], {}, {}
    for question in topic_questions(outline_node):
        row = screen_row(question)
        rows.append(row)
        pairing = row["learning_object_links"]
        group_id = pairing[0]["learning_object_group_id"] if pairing else None
        if group_id is None or not question.paired_by:
            continue
        by_group.setdefault(group_id, []).append(row)
        if fills_minimum(question):
            tiers = counted.setdefault(group_id, {tier: 0 for tier in TIERS})
            tiers[question.thinking_order] += 1
    return rows, by_group, counted


@receiver(pre_delete, sender=LearningObject)
def release_questions(sender, instance, **kwargs):
    """A teacher's or a printed question outlives the learning object it was paired to, unpaired; generated ones go with it."""
    Question.objects.filter(node=instance).exclude(source="generated").update(
        node=None, paired_by="", status="review",
    )
