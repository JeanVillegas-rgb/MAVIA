"""Finds the questions printed in a lesson PDF and hands them to the question bank."""

import hashlib
import logging
import re

from django.db import transaction

from lessons.models import ExtractedQuestion, LearningMaterial
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
_NUMBERED_PREFIX = re.compile(r"^\d+[.)]\s*")
_LETTERED_CHOICES = re.compile(
    r"(?:^|\s|\()([A-Da-d])\s*(?:[.)]|:|-)\s+"
    r"(.+?)(?=(?:\s+\(?[A-Da-d]\s*(?:[.)]|:|-)\s+)|"
    r"(?:\s+(?:answer|correct\s+answer)\s*[:.-])|$)",
    flags=re.I | re.S,
)
# Boolean line check only; unlike _LETTERED_CHOICES, no captures are needed.
_SEPARATE_CHOICE_LINE = re.compile(r"^\s*\(?[A-Da-d]\s*(?:[.)]|:|-)\s+.+?\s*$")


def question_snapshots(material: LearningMaterial) -> list[dict]:
    """Return the stable question handoff stored with a processed PDF."""
    return [
        {
            "id": question.id,
            "prompt": question.prompt,
            "question_type": question.question_type,
            "choices": question.choices,
            "correct_answer": question.correct_answer,
            "order": question.order,
            "source_page": question.source_page,
            "source_block_id": question.source_block_id,
            "source_excerpt": question.source_excerpt,
        }
        for question in material.questions.order_by("order", "id")
    ]


def normalized_question_text(text: str) -> str:
    value = re.sub(r"^\s*(?:question\s*)?\d+\s*[.)-]\s*", "", text or "", flags=re.I)
    value = re.sub(r"[^a-z0-9\s]", " ", value.casefold())
    return re.sub(r"\s+", " ", value).strip()


def question_fingerprint(text: str) -> str:
    return hashlib.sha256(normalized_question_text(text).encode("utf-8")).hexdigest()


def parse_question_structure(prompt: str, source_excerpt: str = "") -> dict:
    """Infer supported question structure without inventing missing choices/answers."""
    raw_prompt = (prompt or "").strip()
    matches = list(_LETTERED_CHOICES.finditer(raw_prompt))
    choice_rows = [
        (match.group(1).upper(), " ".join(match.group(2).split()))
        for match in matches
        if match.group(2).strip()
    ]
    choices = [text for _, text in choice_rows]
    cleaned_prompt = (
        " ".join(raw_prompt[:matches[0].start()].split())
        if matches
        else " ".join(raw_prompt.split())
    )
    lowered_excerpt = (source_excerpt or "").casefold()
    true_false_context = bool(
        re.search(r"\b(?:true\s*(?:or|/)\s*false|write\s+true|write\s+t\s+if)\b", lowered_excerpt)
    )
    standalone_values = {
        line.strip().casefold()
        for line in raw_prompt.splitlines()
        if line.strip()
    }
    if {choice.casefold() for choice in choices} == {"true", "false"} or (
        {"true", "false"} <= standalone_values
    ):
        question_type = ExtractedQuestion.Type.TRUE_FALSE
        choices = ["True", "False"]
    elif len(choices) >= 2:
        question_type = ExtractedQuestion.Type.MULTIPLE_CHOICE
    elif true_false_context:
        question_type = ExtractedQuestion.Type.TRUE_FALSE
        choices = ["True", "False"]
    else:
        question_type = ExtractedQuestion.Type.OPEN_ENDED

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
    if question_type not in {ExtractedQuestion.Type.TRUE_FALSE, ExtractedQuestion.Type.MULTIPLE_CHOICE}:
        issues.append("Choose True/False or Multiple Choice.")
    elif question_type == ExtractedQuestion.Type.TRUE_FALSE:
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


def duplicate_in_topic(material, fingerprint: str):
    """The same question already extracted from another PDF of the topic, if any."""
    topic_questions = ExtractedQuestion.objects.filter(
        material__course_id=material.course_id,
        material__outline_node_id=material.outline_node_id,
    ).order_by("id")
    return next(
        (question for question in topic_questions if question_fingerprint(question.prompt) == fingerprint),
        None,
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
    numbered = _NUMBERED_PREFIX.sub("", stripped)
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
    cleaned = _NUMBERED_PREFIX.sub("", _clean_question_line(text))
    return bool(re.match(
        r"^(?:q\s*:|question\s+\d+\b|who|what|when|where|why|how|which|whose|"
        r"whom|is|are|am|do|does|did|can|could|will|would|should|has|have|had|"
        r"may|might|were|was|answer|calculate|choose|classify|compare|define|"
        r"describe|discuss|explain|give|identify|list|match|name|select|solve|"
        r"state|write)\b",
        cleaned,
        re.I,
    ))


def _is_choice_block(text):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return bool(lines) and all(_SEPARATE_CHOICE_LINE.match(line) for line in lines)


def _is_true_false_choice_block(text):
    return (text or "").strip().casefold() in {"true", "false"}


def _following_choice_text(blocks, index):
    """Collect choice and answer-key blocks immediately after a question."""
    supporting = []
    cursor = index + 1
    while cursor < len(blocks):
        text = (blocks[cursor].get("text") or "").strip()
        if _is_choice_block(text) or _is_true_false_choice_block(text):
            supporting.append(text)
            cursor += 1
            continue
        if supporting and _is_answer_line(text):
            supporting.append(text)
        break
    return "\n".join(supporting)


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
        numbered = bool(_NUMBERED_PREFIX.match(question_only))
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
    blocks = classified_blocks or []
    for index, block in enumerate(blocks):
        category = block.get("category")
        contextual = is_assessment and category in {"lesson_content", "needs_review"}
        if category != "assessment" and not contextual:
            continue
        for prompt in _question_prompts_from_block(
            block.get("text") or "",
            allow_numbered_statements=contextual,
        ):
            excerpt = block.get("text") or prompt
            supporting_text = _following_choice_text(blocks, index)
            parse_text = "\n".join(
                part for part in (prompt, supporting_text) if part
            )
            values = parse_question_structure(parse_text, f"{excerpt}\n{supporting_text}")
            values["prompt"] = (values["prompt"] or "").strip()
            fingerprint = question_fingerprint(values["prompt"])
            if not values["prompt"] or fingerprint in seen:
                continue
            seen.add(fingerprint)
            payloads.append({
                **values,
                "source_page": block.get("page"),
                "source_block_id": block.get("block_id"),
                "source_excerpt": excerpt,
            })
    return payloads


def _sync_extracted_question_to_bank(question: ExtractedQuestion):
    """Mirror one PDF extraction into the question bank, unless the topic already has the same question."""
    from question_generation.models import Question as BankQuestion
    from question_generation.services.question_bank import bank_values

    fingerprint = question_fingerprint(question.prompt)
    content = {
        "question_text": question.prompt,
        **bank_values(question.question_type, question.choices, question.correct_answer),
        "content_fingerprint": fingerprint,
    }
    own = BankQuestion.objects.filter(extracted_question=question).first()
    same_text = BankQuestion.objects.filter(
        outline_node=question.material.outline_node, content_fingerprint=fingerprint,
    ).exclude(status="draft")
    if own is not None:
        same_text = same_text.exclude(pk=own.pk)
    if same_text.exists():
        return own
    if own is None:
        return BankQuestion.objects.create(
            extracted_question=question,
            outline_node=question.material.outline_node,
            status="review",
            source="lesson_pdf",
            **content,
        )
    if not (own.teacher_edited or own.paired_by):
        for field, value in content.items():
            setattr(own, field, value)
        own.save(update_fields=list(content))
    return own


@transaction.atomic
def synchronize_detected_questions(material, classified_blocks):
    """Synchronize extracted PDF questions and their unlabelled bank mirrors."""
    existing = {
        question_fingerprint(question.prompt): question
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
        key = question_fingerprint(payload["prompt"])
        question = existing.get(key) or duplicate_in_topic(material, key)
        if question is None:
            question = ExtractedQuestion.objects.create(material=material, order=order, **payload)
        elif question.material_id != material.id:
            continue
        else:
            for field, value in payload.items():
                setattr(question, field, value)
            question.order = order
            question.save(update_fields=[*payload, "order"])
        _sync_extracted_question_to_bank(question)
        retained_ids.append(question.id)
    material.questions.exclude(id__in=retained_ids).delete()


def add_reviewed_block_questions(material, classified_blocks, block_id):
    """Add only this corrected block's questions; retain all existing reviews."""
    for payload in detected_question_payloads(classified_blocks):
        if payload.get("source_block_id") != block_id:
            continue
        if duplicate_in_topic(material, question_fingerprint(payload["prompt"])):
            continue
        last = material.questions.order_by("-order").first()
        question = ExtractedQuestion.objects.create(
            material=material, order=last.order + 1 if last else 0, **payload,
        )
        _sync_extracted_question_to_bank(question)
