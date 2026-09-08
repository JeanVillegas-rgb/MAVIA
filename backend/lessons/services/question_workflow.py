"""Shared intake, validation, classification and adaptive-bank synchronization."""

from functools import lru_cache
import hashlib
import re

from django.db import transaction

from lessons.models import Question, QuestionLearningObjectLink


def normalized_question_text(text: str) -> str:
    value = re.sub(r"^\s*(?:question\s*)?\d+\s*[.)-]\s*", "", text or "", flags=re.I)
    value = re.sub(r"[^a-z0-9\s]", " ", value.casefold())
    return re.sub(r"\s+", " ", value).strip()


def question_fingerprint(text: str) -> str:
    return hashlib.sha256(normalized_question_text(text).encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def _bloom_classifier():
    from question_generation.services.bloom_classifier import BloomClassifier
    return BloomClassifier()


def classify_question(text: str) -> dict:
    return _bloom_classifier().classify(text)


def parse_question_structure(prompt: str, source_excerpt: str = "") -> dict:
    """Infer supported question structure without inventing missing choices/answers."""
    raw_prompt = (prompt or "").strip()
    choice_pattern = re.compile(
        r"(?:^|\s)([A-Da-d])\s*[.)]\s*(.+?)(?=(?:\s+[A-Da-d]\s*[.)]\s)|$)",
        flags=re.S,
    )
    matches = list(choice_pattern.finditer(raw_prompt))
    choice_rows = [
        (match.group(1).upper(), " ".join(match.group(2).split()))
        for match in matches
        if match.group(2).strip()
    ]
    choices = [text for _, text in choice_rows if text]
    cleaned_prompt = (
        " ".join(raw_prompt[:matches[0].start()].split())
        if matches
        else " ".join(raw_prompt.split())
    )
    lowered_excerpt = (source_excerpt or "").casefold()
    true_false_context = bool(
        re.search(r"\b(?:true\s*(?:or|/)\s*false|write\s+true|write\s+t\s+if)\b", lowered_excerpt)
    )
    if {choice.casefold() for choice in choices} == {"true", "false"}:
        question_type = Question.Type.TRUE_FALSE
        choices = ["True", "False"]
    elif len(choices) >= 2:
        question_type = Question.Type.MULTIPLE_CHOICE
    elif true_false_context:
        question_type = Question.Type.TRUE_FALSE
        choices = ["True", "False"]
    else:
        question_type = Question.Type.OPEN_ENDED

    correct_answer = ""
    answer_match = re.search(
        r"\b(?:answer|correct\s+answer)\s*[:.-]\s*([A-D]|true|false|[^\r\n]+)",
        source_excerpt or "",
        flags=re.I,
    )
    if answer_match:
        raw_answer = answer_match.group(1).strip()
        if len(raw_answer) == 1 and raw_answer.upper() in "ABCD":
            lookup = dict(choice_rows)
            correct_answer = lookup.get(raw_answer.upper(), "")
        else:
            correct_answer = raw_answer.title() if raw_answer.casefold() in {"true", "false"} else raw_answer

    return {
        "prompt": cleaned_prompt,
        "question_type": question_type,
        "choices": choices,
        "correct_answer": correct_answer,
    }


def validation_issues(question_type: str, choices, correct_answer: str) -> list[str]:
    issues = []
    choices = choices if isinstance(choices, list) else []
    if question_type not in {Question.Type.TRUE_FALSE, Question.Type.MULTIPLE_CHOICE}:
        issues.append("Choose True/False or Multiple Choice.")
    elif question_type == Question.Type.TRUE_FALSE:
        if {str(choice).casefold() for choice in choices} != {"true", "false"}:
            issues.append("True/False questions must contain True and False choices.")
        if str(correct_answer).casefold() not in {"true", "false"}:
            issues.append("Set the correct answer to True or False.")
    else:
        clean_choices = [str(choice).strip() for choice in choices if str(choice).strip()]
        if not 2 <= len(clean_choices) <= 4:
            issues.append("Provide between two and four answer choices.")
        if not correct_answer or correct_answer not in clean_choices:
            issues.append("Select a correct answer from the choices.")
    return issues


def enriched_question_values(prompt, question_type, choices, correct_answer) -> dict:
    prompt = (prompt or "").strip()
    classification = classify_question(prompt)
    issues = validation_issues(question_type, choices, correct_answer)
    return {
        "prompt": prompt,
        "question_type": question_type,
        "choices": choices,
        "correct_answer": correct_answer,
        "content_fingerprint": question_fingerprint(prompt),
        "bloom_level": classification["bloom_level"],
        "thinking_order": classification["thinking_order"] or "",
        "difficulty": classification["difficulty"],
        "category": classification["category"],
        "validation_status": (
            Question.ValidationStatus.NEEDS_REVIEW if issues else Question.ValidationStatus.READY
        ),
        "validation_issues": issues,
    }


def duplicate_for_topic(course_id, outline_node_id, fingerprint: str, *, exclude_id=None):
    query = Question.objects.filter(
        material__course_id=course_id,
        material__outline_node_id=outline_node_id,
    )
    if exclude_id:
        query = query.exclude(pk=exclude_id)
    existing = query.filter(content_fingerprint=fingerprint).order_by("id").first()
    if existing:
        return existing
    for question in query.filter(content_fingerprint="").order_by("id"):
        question.content_fingerprint = question_fingerprint(question.prompt)
        question.save(update_fields=["content_fingerprint"])
        if question.content_fingerprint == fingerprint:
            return question
    return None


def duplicate_in_topic(material, fingerprint: str, *, exclude_id=None):
    return duplicate_for_topic(
        material.course_id,
        material.outline_node_id,
        fingerprint,
        exclude_id=exclude_id,
    )


def question_is_approved(question: Question) -> bool:
    return question.learning_object_links.filter(
        review_status__in=(
            QuestionLearningObjectLink.ReviewStatus.AUTO_CONFIRMED,
            QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED,
        ),
        is_primary=True,
    ).exists()


@transaction.atomic
def sync_question_to_adaptive(question: Question):
    """Create/update the learner-facing row only for valid, approved questions."""
    if question.validation_status != Question.ValidationStatus.READY or not question_is_approved(question):
        if question.adaptive_question_id:
            adaptive_id = question.adaptive_question_id
            question.adaptive_question = None
            question.save(update_fields=["adaptive_question"])
            from question_generation.models import GeneratedQuestion
            GeneratedQuestion.objects.filter(pk=adaptive_id).delete()
        return None
    link = question.learning_object_links.filter(
        review_status__in=(
            QuestionLearningObjectLink.ReviewStatus.AUTO_CONFIRMED,
            QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED,
        ),
        is_primary=True,
    ).select_related("learning_object").first()
    if link is None:
        return None

    from question_generation.models import GeneratedQuestion
    question_format = "TF" if question.question_type == Question.Type.TRUE_FALSE else "MCQ"
    choice_map = None
    adaptive_answer = question.correct_answer
    if question_format == "MCQ":
        choice_map = {
            chr(65 + index): choice
            for index, choice in enumerate(question.choices or [])
        }
        adaptive_answer = next(
            (letter for letter, value in choice_map.items() if value == question.correct_answer),
            "",
        )
    defaults = {
        "node": link.learning_object,
        "question_text": question.prompt,
        "question_format": question_format,
        "choices": choice_map,
        "correct_answer": adaptive_answer,
        "bloom_level": question.bloom_level,
        "thinking_order": question.thinking_order,
        "difficulty": question.difficulty,
        "category": question.category,
        "status": "final",
    }
    if question.adaptive_question_id:
        GeneratedQuestion.objects.filter(pk=question.adaptive_question_id).update(**defaults)
        adaptive = GeneratedQuestion.objects.get(pk=question.adaptive_question_id)
    else:
        adaptive = GeneratedQuestion.objects.create(**defaults)
        question.adaptive_question = adaptive
        question.save(update_fields=["adaptive_question"])
    return adaptive


@transaction.atomic
def mirror_generated_questions(node, generated_questions):
    """Expose generated adaptive questions in the same teacher review list."""
    Question.objects.filter(
        material=node.material,
        source_type=Question.SourceType.GENERATED,
        adaptive_question__isnull=True,
    ).delete()
    for generated in generated_questions:
        choice_map = generated.choices or {}
        choices = [choice_map[key] for key in sorted(choice_map)] if isinstance(choice_map, dict) else []
        correct_answer = choice_map.get(generated.correct_answer, "") if isinstance(choice_map, dict) else generated.correct_answer
        fingerprint = question_fingerprint(generated.question_text)
        question = duplicate_in_topic(node.material, fingerprint)
        if question is None:
            question = Question.objects.create(
                material=node.material,
                source_type=Question.SourceType.GENERATED,
                prompt=generated.question_text,
                question_type=(Question.Type.TRUE_FALSE if generated.question_format == "TF" else Question.Type.MULTIPLE_CHOICE),
                choices=["True", "False"] if generated.question_format == "TF" else choices,
                correct_answer=correct_answer,
                content_fingerprint=fingerprint,
                bloom_level=generated.bloom_level,
                thinking_order=generated.thinking_order,
                difficulty=generated.difficulty,
                category=generated.category,
                validation_status=Question.ValidationStatus.READY,
                validation_issues=[],
                adaptive_question=generated,
                order=node.material.questions.count(),
                source_excerpt="Generated from a confirmed learning object.",
            )
        elif question.adaptive_question_id is None:
            question.adaptive_question = generated
            question.save(update_fields=["adaptive_question"])
        elif question.adaptive_question_id != generated.id:
            # A PDF/manual version is already present in the adaptive bank.
            # Keep that canonical question and discard this generated copy.
            generated.delete()
            continue
        QuestionLearningObjectLink.objects.update_or_create(
            question=question,
            learning_object=node,
            defaults={
                "relevance_score": 1.0,
                "method": "generated_from_object",
                "is_primary": True,
                "review_status": QuestionLearningObjectLink.ReviewStatus.AUTO_CONFIRMED,
            },
        )
