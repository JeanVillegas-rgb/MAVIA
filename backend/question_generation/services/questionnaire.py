"""A teacher's questionnaire PDF: read with the lesson pipeline's question reader, then labelled, paired and checked into the bank."""

import logging

from django.db import transaction

from lessons.services.block_classifier import classify_blocks
from lessons.services.lesson_pdf_extractor import extract_lesson_pdf_blocks
from lessons.services.question_workflow import detected_question_payloads, question_fingerprint
from question_generation.models import Question, QuestionFile

from .grounding import build_index
from .question_bank import bank_values, duplicate_in_topic, settle, structure_issues
from .teacher_questions import QuestionError, _apply, _found_place, _label

logger = logging.getLogger(__name__)

NOT_A_PDF = "Upload the questionnaire as a PDF file."
NO_TEXT = "No text could be read from this PDF. Scanned questionnaires are not supported; upload a PDF with selectable text."


def _read_items(path):
    """The questions the lesson pipeline's reader finds in the file, as it found them."""
    blocks = extract_lesson_pdf_blocks(path)[0]
    if not any((block.get("text") or "").strip() for block in blocks):
        raise QuestionError(NO_TEXT)
    return detected_question_payloads(classify_blocks(blocks))


def upload_questionnaire(node, upload):
    """Save the file, add each item it holds to the topic's bank; returns counts for the teacher."""
    name = getattr(upload, "name", "") or ""
    head = upload.read(5)
    upload.seek(0)
    if not name.lower().endswith(".pdf") or head != b"%PDF-":
        raise QuestionError(NOT_A_PDF)

    with transaction.atomic():
        question_file = QuestionFile.objects.create(outline_node=node, file=upload)
    try:
        items = _read_items(question_file.file.path)
    except Exception:
        question_file.file.delete(save=False)
        question_file.delete()
        raise

    counts = {"found": len(items), "added": 0, "paired": 0, "held": 0, "already_in_topic": 0}
    index = build_index(node) if items else None
    for item in items:
        if duplicate_in_topic(node, question_fingerprint(item["prompt"])) is not None:
            counts["already_in_topic"] += 1
            continue
        question = Question(
            outline_node=node,
            source="questionnaire",
            question_file=question_file,
            question_text=item["prompt"],
            **bank_values(item["question_type"], item["choices"], item["correct_answer"]),
        )
        _label(question)
        if structure_issues(question):
            settle(question)
        else:
            _apply(question, _found_place(question, index))
        counts["added"] += 1
        counts["paired" if question.status == "final" else "held"] += 1

    if not counts["added"]:
        question_file.file.delete(save=False)
        question_file.delete()
    logger.info(
        "[Questions] topic %s  questionnaire %s: %s found, %s added (%s paired, %s held), %s already in the topic",
        node.id, name, counts["found"], counts["added"], counts["paired"], counts["held"],
        counts["already_in_topic"],
    )
    return counts


def topic_questionnaires(node):
    """The questionnaires uploaded to the topic, with how many questions each still has and how many the teacher worked on."""
    files = []
    for question_file in QuestionFile.objects.filter(outline_node=node).order_by("uploaded_at", "id"):
        questions = list(question_file.questions.all())
        files.append({
            "id": question_file.id,
            "name": question_file.file.name.rsplit("/", 1)[-1],
            "uploaded_at": question_file.uploaded_at,
            "questions": len(questions),
            "reviewed": sum(
                question.teacher_edited or question.paired_by == "teacher" or question.check_result == "kept"
                for question in questions
            ),
        })
    return files


def delete_questionnaire(node, file_id):
    """Delete an uploaded questionnaire and every question that came from it."""
    question_file = QuestionFile.objects.filter(pk=file_id, outline_node=node).first()
    if question_file is None:
        raise QuestionError("Questionnaire was not found in this topic.", status=404)
    question_file.file.delete(save=False)
    question_file.delete()


def outcome_message(counts):
    """What the upload did, in a sentence for the teacher."""
    if not counts["found"]:
        return "No questions were found in this PDF."
    parts = [f'{counts["added"]} of {counts["found"]} question{"" if counts["found"] == 1 else "s"} added']
    if counts["added"]:
        parts.append(f'{counts["paired"]} paired to a concept, {counts["held"]} held for your review')
    if counts["already_in_topic"]:
        parts.append(f'{counts["already_in_topic"]} skipped because the topic already has them')
    return "; ".join(parts) + "."
