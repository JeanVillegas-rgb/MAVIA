"""Shared intake, validation, classification and adaptive-bank synchronization."""

import hashlib
import logging
import re

from django.db import models, transaction

from lessons.models import LearningMaterial, Question, QuestionLearningObjectLink
from .block_classifier import detect_instructional_document_role
from .concept_grouper import normalize_learning_object_title

logger = logging.getLogger(__name__)


_QUESTION_CONTAINER_LABELS = {
    "ask", "assessment", "assessments", "check your knowledge",
    "check your understanding", "directions", "exercise", "exercises",
    "instructions", "knowledge check", "practice", "practice questions",
    "question", "questions", "quiz", "review questions", "teacher check",
    "test", "true false", "true or false", "worksheet",
}


CONFIRMED_PAIRING_STATUSES = (
    QuestionLearningObjectLink.ReviewStatus.AUTO_CONFIRMED,
    QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED,
)


def question_snapshots(material: LearningMaterial) -> list[dict]:
    """Return the stable question handoff stored with a processed PDF."""
    snapshots = []
    questions = material.questions.prefetch_related(
        "learning_object_links__learning_object__group"
    ).order_by("order", "id")
    for question in questions:
        links = [
            {
                "learning_object_id": link.learning_object_id,
                "learning_object_group_id": link.learning_object.group_id,
                "relevance_score": link.relevance_score,
                "method": link.method,
                "is_primary": link.is_primary,
                "review_status": link.review_status,
            }
            for link in question.learning_object_links.all()
            if link.review_status in CONFIRMED_PAIRING_STATUSES
        ]
        snapshots.append({
            "id": question.id,
            "prompt": question.prompt,
            "source_type": question.source_type,
            "question_type": question.question_type,
            "choices": question.choices,
            "correct_answer": question.correct_answer,
            "bloom_level": question.bloom_level,
            "thinking_order": question.thinking_order,
            "difficulty": question.difficulty,
            "category": question.category,
            "validation_status": question.validation_status,
            "validation_issues": question.validation_issues,
            "order": question.order,
            "source_page": question.source_page,
            "source_block_id": question.source_block_id,
            "source_excerpt": question.source_excerpt,
            "learning_object_links": links,
        })
    return snapshots


def normalized_question_text(text: str) -> str:
    value = re.sub(r"^\s*(?:question\s*)?\d+\s*[.)-]\s*", "", text or "", flags=re.I)
    value = re.sub(r"[^a-z0-9\s]", " ", value.casefold())
    return re.sub(r"\s+", " ", value).strip()


def question_fingerprint(text: str) -> str:
    return hashlib.sha256(normalized_question_text(text).encode("utf-8")).hexdigest()


def classify_question(text: str) -> dict:
    # The generation pipeline's instance: a second copy of RoBERTa here was
    # loaded again by the same Questions step.
    from question_generation.services.pipeline import _get_classifier
    return _get_classifier().classify(text)


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


LABEL_FIELDS = ("bloom_level", "thinking_order", "category")


def enriched_question_values(prompt, question_type, choices, correct_answer, *, classify=True) -> dict:
    """Database-ready values for one question.

    ``classify=False`` leaves the Bloom level, LOTS/HOTS and category blank:
    questions printed in a PDF are stored at upload and labelled in the
    Questions step, where every question-related process happens.
    """
    prompt = (prompt or "").strip()
    classification = (
        classify_question(prompt) if classify
        else {"bloom_level": "", "thinking_order": "", "category": ""}
    )
    issues = validation_issues(question_type, choices, correct_answer)
    return {
        "prompt": prompt,
        "question_type": question_type,
        "choices": choices,
        "correct_answer": correct_answer,
        "content_fingerprint": question_fingerprint(prompt),
        "bloom_level": classification["bloom_level"],
        "thinking_order": classification["thinking_order"] or "",
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


TIERS = ("LOT", "HOT")


def counts_toward_minimum(question: Question, link: QuestionLearningObjectLink) -> bool:
    """Whether ``question``, paired to a concept by ``link``, fills its minimum.

    A generated question counts once paired. A teacher's own question counts
    once the teacher pairs it. A printed one counts only after the teacher has
    both edited it and confirmed its pairing: it arrives as extraction left it,
    and the matcher's confidence is not the teacher's.
    """
    if (
        question.thinking_order not in TIERS
        or question.validation_status != Question.ValidationStatus.READY
        or not link.is_primary
    ):
        return False
    status = link.review_status
    if question.source_type == Question.SourceType.GENERATED:
        return status in (
            QuestionLearningObjectLink.ReviewStatus.AUTO_CONFIRMED,
            QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED,
        )
    if status != QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED:
        return False
    return question.source_type == Question.SourceType.MANUAL or question.teacher_edited


def concept_tier_counts(learning_object) -> dict:
    """LOTS and HOTS questions counting toward the minimum of the concept
    ``learning_object`` belongs to (just the object, when it has none)."""
    scope = (
        models.Q(learning_object__group_id=learning_object.group_id)
        if learning_object.group_id
        else models.Q(learning_object=learning_object)
    )
    links = QuestionLearningObjectLink.objects.filter(
        scope,
        learning_object__material__generated_json__learning_objects_confirmed=True,
    ).select_related("question")
    counted = {link.question for link in links if counts_toward_minimum(link.question, link)}
    return {tier: sum(question.thinking_order == tier for question in counted) for tier in TIERS}


@transaction.atomic
def is_given_to_learners(question: Question) -> bool:
    """Labelled, and at a level the learner quiz has a tier for.

    A question not labelled yet waits for the Questions step. One labelled
    "create" has no LOTS/HOTS tier, so it stays in the teacher's bank only --
    the same as generated "create" questions, which are never kept at all.
    """
    return bool(question.bloom_level and question.thinking_order)


def label_printed_questions(outline_node) -> int:
    """Label every printed question in the topic that is not labelled yet.

    Returns how many were labelled. Each one reaches the learners' bank once
    labelled, if its pairing and level allow it.
    """
    pending = list(
        Question.objects.filter(
            material__outline_node=outline_node,
            source_type=Question.SourceType.PDF,
            bloom_level="",
        ).order_by("material_id", "order", "id")
    )
    for question in pending:
        classification = classify_question(question.prompt)
        question.bloom_level = classification["bloom_level"]
        question.thinking_order = classification["thinking_order"] or ""
        question.category = classification["category"]
        question.save(update_fields=list(LABEL_FIELDS))
        sync_question_to_adaptive(question)
    if pending:
        logger.info(
            "[Questions] topic %s  labelled %s printed question(s) with Bloom level, LOTS/HOTS and category",
            outline_node.id, len(pending),
        )
    return len(pending)


def sync_question_to_adaptive(question: Question):
    """Create/update the learner-facing row only for valid, approved questions."""
    if (
        question.validation_status != Question.ValidationStatus.READY
        or not question_is_approved(question)
        or not is_given_to_learners(question)
    ):
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


def delete_generated_questions_for(learning_object):
    """Delete the questions generated from this one learning object, and no others.

    Call it before deleting the object. A generated question linked to another
    surviving object as well -- the same text generated twice -- is kept, as is
    any question a teacher re-paired to a different concept. Questions extracted
    from a PDF are never deleted here; they only lose their link to this object.
    """
    from question_generation.models import GeneratedQuestion

    generated_here = set(
        QuestionLearningObjectLink.objects.filter(
            learning_object=learning_object,
            method="generated_from_object",
        ).values_list("question_id", flat=True)
    )
    still_needed = set(
        QuestionLearningObjectLink.objects.filter(question_id__in=generated_here)
        .exclude(learning_object=learning_object)
        .values_list("question_id", flat=True)
    )
    doomed = list(
        Question.objects.filter(
            pk__in=generated_here - still_needed,
            source_type=Question.SourceType.GENERATED,
        )
    )
    adaptive_ids = [item.adaptive_question_id for item in doomed if item.adaptive_question_id]
    Question.objects.filter(pk__in=[item.id for item in doomed]).delete()
    GeneratedQuestion.objects.filter(
        models.Q(node=learning_object) | models.Q(pk__in=adaptive_ids)
    ).delete()
    return len(doomed)


@transaction.atomic
def mirror_generated_questions(node, generated_questions):
    """Expose generated adaptive questions in the same teacher review list."""
    # Clear only this concept's stale generated rows (and ones with no concept
    # at all). Clearing every generated row in the file without a learner-facing
    # copy also removed other concepts' questions a teacher had sent back for
    # review.
    stale_ids = list(
        Question.objects.filter(
            material=node.material,
            source_type=Question.SourceType.GENERATED,
            adaptive_question__isnull=True,
        )
        .filter(
            models.Q(learning_object_links__learning_object=node)
            | models.Q(learning_object_links__isnull=True)
        )
        .values_list("id", flat=True)
    )
    Question.objects.filter(pk__in=stale_ids).delete()
    for generated in generated_questions:
        choice_map = generated.choices or {}
        true_false = generated.question_format == "TF"
        raw_answer = (generated.correct_answer or "").strip()
        if true_false:
            choices = ["True", "False"]
            # A true/false answer is the answer, not a key into the choices --
            # and the map is empty, so looking it up there found nothing and
            # left the question with no answer key at all.
            correct_answer = raw_answer.capitalize()
        elif isinstance(choice_map, dict):
            choices = [choice_map[key] for key in sorted(choice_map)]
            # The key is what the generator returns, but an answer already
            # written out in full is still an answer: a question must not reach
            # a teacher with nothing marked correct.
            correct_answer = choice_map.get(raw_answer) or (raw_answer if raw_answer in choices else "")
        else:
            choices = []
            correct_answer = raw_answer
        fingerprint = question_fingerprint(generated.question_text)
        question = duplicate_in_topic(node.material, fingerprint)
        if question is None:
            question = Question.objects.create(
                material=node.material,
                source_type=Question.SourceType.GENERATED,
                prompt=generated.question_text,
                question_type=(Question.Type.TRUE_FALSE if generated.question_format == "TF" else Question.Type.MULTIPLE_CHOICE),
                choices=choices,
                correct_answer=correct_answer,
                content_fingerprint=fingerprint,
                bloom_level=generated.bloom_level,
                thinking_order=generated.thinking_order,
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


def _is_answer_line(text):
    label = normalize_learning_object_title(text)
    return bool(
        re.match(
            r"^(?:a\s*:|(?:answer|answers|correct answer|expected answer|"
            r"sample answer|suggested answer)\b(?:\s*[:.-])?)",
            (text or "").strip(),
            flags=re.IGNORECASE,
        )
        or label in {
            "answer", "answers", "correct answer", "expected answer",
            "sample answer", "suggested answer",
        }
    )


def _clean_question_line(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    return re.sub(r"^[A-Za-z]?\s*[\x00-\x1f\u2022\u25cf\u25aa]+\s*", "", text).strip()


def _is_form_identity_line(text):
    label = normalize_learning_object_title(text)
    fields = {"date", "name", "score", "section", "student"}
    return bool(re.search(r"_{3,}", text or "") and len(set(label.split()) & fields) >= 2)


def _is_question_line(text, *, allow_numbered_statement=False):
    stripped = _clean_question_line(text)
    if not stripped or _is_answer_line(stripped) or _is_form_identity_line(stripped):
        return False
    if stripped.endswith("?"):
        return True
    if re.match(r"^(?:q\s*:|q\d+\b|question\s+\d+\b)", stripped, re.I):
        return True
    numbered = re.sub(r"^\d+[.)]\s*", "", stripped)
    if allow_numbered_statement and numbered != stripped:
        return True
    if numbered != stripped and re.match(
        r"^(?:answer|calculate|choose|classify|compare|define|describe|discuss|"
        r"explain|give|identify|list|match|name|select|solve|state|write)\b",
        numbered,
        re.I,
    ):
        return True
    if re.search(r"_{3,}", stripped):
        return bool(
            numbered != stripped
            or (stripped[:1].isupper() and len(normalize_learning_object_title(stripped).split()) >= 2)
        )
    return len(re.findall(r"(?:^|\s)[A-Da-d][.)]\s+", stripped)) >= 2


def _starts_new_question(text):
    cleaned = re.sub(r"^\d+[.)]\s*", "", _clean_question_line(text))
    return bool(re.match(
        r"^(?:q\s*:|question\s+\d+\b|who|what|when|where|why|how|which|whose|"
        r"whom|is|are|am|do|does|did|can|could|will|would|should|has|have|had|"
        r"may|might|were|was|answer|calculate|choose|classify|compare|define|"
        r"describe|discuss|explain|give|identify|list|match|name|select|solve|"
        r"state|write)\b",
        cleaned,
        re.I,
    ))


def _question_prompts_from_block(text, *, allow_numbered_statements=False):
    """Extract authored questions without headings or supplied answers."""
    lines = [_clean_question_line(line) for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    prompts, current = [], []

    def flush():
        if current:
            prompt = "\n".join(current).strip()
            if _is_question_line(prompt, allow_numbered_statement=allow_numbered_statements):
                prompts.append(prompt)
            current.clear()

    for line in lines:
        label = normalize_learning_object_title(line)
        if label in _QUESTION_CONTAINER_LABELS:
            continue
        if _is_answer_line(line):
            flush()
            continue
        question_only = re.split(
            r"\s+(?:A|Answer)\s*:\s*", line, maxsplit=1, flags=re.I,
        )[0].strip()
        starts = _is_question_line(
            question_only,
            allow_numbered_statement=allow_numbered_statements,
        )
        phrase = _starts_new_question(question_only)
        numbered = bool(re.match(r"^\d+[.)]\s*", question_only))
        choice = bool(re.match(r"^[A-Da-d][.)]\s+", question_only))
        if numbered and current:
            flush()
        if phrase and current and _clean_question_line("\n".join(current)).endswith("?"):
            flush()
        if starts or phrase or (current and choice):
            current.append(question_only)
        elif current and not re.match(r"^(?:expected|correct|sample|suggested)\s+answer\b", label):
            current.append(question_only)
    flush()
    return prompts


def detected_question_payloads(classified_blocks):
    """Return database-ready questions extracted from classified PDF blocks."""
    payloads = []
    is_assessment = detect_instructional_document_role(classified_blocks or []) == "assessment"
    seen = set()
    for block in classified_blocks or []:
        category = block.get("category")
        contextual = is_assessment and category in {"lesson_content", "needs_review"}
        if category != "assessment" and not contextual:
            continue
        for prompt in _question_prompts_from_block(
            block.get("text") or "",
            allow_numbered_statements=contextual,
        ):
            excerpt = block.get("text") or prompt
            values = enriched_question_values(
                **parse_question_structure(prompt, excerpt),
                classify=False,
            )
            fingerprint = values["content_fingerprint"]
            if not fingerprint or fingerprint in seen:
                continue
            seen.add(fingerprint)
            payloads.append({
                **values,
                "source_type": Question.SourceType.PDF,
                "source_page": block.get("page"),
                "source_block_id": block.get("block_id"),
                "source_excerpt": excerpt,
            })
    return payloads


@transaction.atomic
def synchronize_detected_questions(material, classified_blocks):
    """Synchronize extracted PDF questions and rebuild their concept links."""
    existing = {
        question.content_fingerprint or question_fingerprint(question.prompt): question
        for question in material.questions.all()
    }
    retained_ids = []
    payloads = detected_question_payloads(classified_blocks)
    if payloads:
        logger.info(
            "[Upload] PDF %s  %s printed question(s) found; labelled in the Questions step",
            material.id,
            len(payloads),
        )
    for order, payload in enumerate(payloads):
        key = payload["content_fingerprint"]
        question = existing.get(key) or duplicate_in_topic(material, key)
        if question is None:
            question = Question.objects.create(material=material, order=order, **payload)
        elif question.material_id != material.id:
            continue
        else:
            fields = [
                field for field, value in payload.items()
                if not (field in LABEL_FIELDS and not value)
            ]
            for field in fields:
                setattr(question, field, payload[field])
            question.order = order
            question.save(update_fields=[*fields, "order"])
        retained_ids.append(question.id)
    material.questions.filter(source_type=Question.SourceType.PDF).exclude(
        id__in=retained_ids,
    ).delete()
    from .question_linker import refresh_question_learning_object_links

    refresh_question_learning_object_links(material)
