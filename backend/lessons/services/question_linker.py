"""Connect lesson questions to the concepts that can answer them."""

import logging
import os
import re

from lessons.models import LearningObject, Question, QuestionLearningObjectLink

from .concept_grouper import learning_objects_are_confirmed


logger = logging.getLogger(__name__)

CONFIRMED_QUESTION_PAIRING_STATUSES = (
    QuestionLearningObjectLink.ReviewStatus.AUTO_CONFIRMED,
    QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED,
)
TEACHER_QUESTION_PAIRING_STATUSES = (
    QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED,
    QuestionLearningObjectLink.ReviewStatus.TEACHER_UNPAIRED,
)
QUESTION_PAIRING_METHOD = "sbert_concept"
GENERATED_PAIRING_METHOD = "generated_from_object"
_QUESTION_NUMBER = re.compile(r"^\s*(?:question\s*)?\d+\s*(?:[.)-]\s*|$)", re.I)


def _env_score(name, default):
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(1.0, max(0.0, value))


def question_pairing_debug_configuration():
    """Expose the active semantic-pairing thresholds to the teacher UI."""
    auto_threshold = _env_score("QUESTION_PAIR_AUTO_THRESHOLD", 0.55)
    return {
        "method": QUESTION_PAIRING_METHOD,
        "weights": {},
        "thresholds": {
            "auto_confirm": auto_threshold,
            "teacher_review": min(
                auto_threshold,
                _env_score("QUESTION_PAIR_REVIEW_THRESHOLD", 0.30),
            ),
            "minimum_margin": _env_score("QUESTION_PAIR_MINIMUM_MARGIN", 0.05),
        },
    }


def is_empty_prompt(prompt):
    return not re.sub(r"[\W_]+", "", _QUESTION_NUMBER.sub("", prompt or ""))


def _question_encoder():
    from .embedding_service import (
        SemanticUnavailable,
        semantic_mode,
        sentence_embedding_runtime,
    )

    if semantic_mode() == "legacy":
        raise SemanticUnavailable("semantic grouping is switched off")
    return sentence_embedding_runtime()


def _rank_concepts(question_vector, members):
    """Return each concept's strongest matching learning object."""
    best = {}
    for learning_object, vector in members:
        score = sum(a * b for a, b in zip(question_vector, vector))
        key = learning_object.group_id or f"object:{learning_object.id}"
        if key not in best or score > best[key][0]:
            best[key] = (score, learning_object)
    return sorted(best.values(), key=lambda row: (-row[0], row[1].order, row[1].id))


def _questions_open_to_pairing(material):
    """Keep generated-question ownership and return only pairable questions."""
    questions = list(material.questions.order_by("order", "id"))
    protected_ids = set(
        QuestionLearningObjectLink.objects.filter(
            question__material=material,
            method=GENERATED_PAIRING_METHOD,
        ).values_list("question_id", flat=True)
    )
    linked_ids = set(
        QuestionLearningObjectLink.objects.filter(
            question__material=material,
        ).values_list("question_id", flat=True)
    )
    open_questions, orphaned = [], []
    for question in questions:
        if question.id in protected_ids:
            continue
        if question.source_type == Question.SourceType.GENERATED:
            if question.id not in linked_ids:
                orphaned.append(question)
            continue
        open_questions.append(question)

    if orphaned:
        from question_generation.models import GeneratedQuestion

        adaptive_ids = [row.adaptive_question_id for row in orphaned if row.adaptive_question_id]
        Question.objects.filter(pk__in=[row.id for row in orphaned]).delete()
        if adaptive_ids:
            GeneratedQuestion.objects.filter(pk__in=adaptive_ids).delete()
        logger.info(
            "[Questions] PDF %s removed %s generated question(s) whose object was deleted",
            material.id,
            len(orphaned),
        )
    return open_questions


def refresh_question_learning_object_links(material):
    """Pair printed/manual questions while preserving teacher decisions."""
    questions = _questions_open_to_pairing(material)
    learning_objects = (
        list(material.learning_objects.order_by("order", "id"))
        if learning_objects_are_confirmed(material)
        else []
    )
    if not learning_objects and material.outline_node_id is not None:
        learning_objects = list(
            LearningObject.objects.filter(
                material__outline_node_id=material.outline_node_id,
                material__generated_json__learning_objects_confirmed=True,
            )
            .exclude(material_id=material.id)
            .select_related("material", "group")
            .order_by("material_id", "order", "id")
        )
    if not learning_objects:
        QuestionLearningObjectLink.objects.filter(question__in=questions).exclude(
            review_status__in=TEACHER_QUESTION_PAIRING_STATUSES,
        ).delete()
        return

    from .embedding_service import SemanticUnavailable
    from .question_workflow import sync_question_to_adaptive

    open_questions = []
    for question in questions:
        existing = question.learning_object_links.order_by(
            "-is_primary", "-relevance_score", "id",
        ).first()
        if existing and existing.review_status in TEACHER_QUESTION_PAIRING_STATUSES:
            continue
        if is_empty_prompt(question.prompt):
            question.learning_object_links.all().delete()
            sync_question_to_adaptive(question)
            continue
        open_questions.append(question)
    if not open_questions:
        return

    try:
        engine = _question_encoder()
        members = [item for item in learning_objects if engine.supports(item.content)]
        member_vectors = engine.embeddings([item.content for item in members])
        prompts = [item for item in open_questions if engine.supports(item.prompt)]
        question_vectors = engine.embeddings([item.prompt for item in prompts])
    except SemanticUnavailable as exc:
        logger.warning("[Questions] PDF %s questions left unchanged: %s", material.id, exc)
        return

    thresholds = question_pairing_debug_configuration()["thresholds"]
    candidates = list(zip(members, member_vectors))
    for question, vector in zip(prompts, question_vectors):
        ranked = _rank_concepts(vector, candidates)
        if not ranked:
            continue
        score, best = ranked[0]
        margin = score - ranked[1][0] if len(ranked) > 1 else score
        if score >= thresholds["auto_confirm"] and margin >= thresholds["minimum_margin"]:
            review_status = QuestionLearningObjectLink.ReviewStatus.AUTO_CONFIRMED
        elif score >= thresholds["teacher_review"]:
            review_status = QuestionLearningObjectLink.ReviewStatus.PENDING_REVIEW
        else:
            review_status = QuestionLearningObjectLink.ReviewStatus.UNMATCHED
        question.learning_object_links.all().delete()
        QuestionLearningObjectLink.objects.create(
            question=question,
            learning_object=best,
            relevance_score=round(score, 6),
            method=QUESTION_PAIRING_METHOD,
            is_primary=review_status == QuestionLearningObjectLink.ReviewStatus.AUTO_CONFIRMED,
            review_status=review_status,
        )
        question.refresh_from_db()
        sync_question_to_adaptive(question)
