"""What a teacher does to a question on the Questions screen: add, edit, pair to a concept, delete."""

import logging

from lessons.models import ExtractedQuestion, LearningObjectGroup
from lessons.services.question_workflow import question_fingerprint, validation_issues
from question_generation.models import Question

from .grounding import build_index, verify
from .pairing import KEY_CONFLICT, PAIRED, UNVERIFIED, pair_question
from .pipeline import _get_classifier, _is_concept_source, concept_name, concept_source_text
from .question_bank import (
    CONFIRMED_OBJECTS,
    can_keep,
    bank_values,
    duplicate_in_topic,
    issues,
    screen_values,
    settle,
    structure_issues,
    topic_questions,
)

logger = logging.getLogger(__name__)

SUPPORTED_TYPES = (ExtractedQuestion.Type.TRUE_FALSE, ExtractedQuestion.Type.MULTIPLE_CHOICE)


class QuestionError(ValueError):
    """A request the teacher has to correct, with the HTTP status to answer with."""

    def __init__(self, detail, status=400, **extra):
        super().__init__(detail)
        self.detail = detail
        self.status = status
        self.extra = extra


def _question(node, question_id):
    question = topic_questions(node).filter(pk=question_id).first()
    if question is None:
        raise QuestionError("Question was not found in this outline node.", status=404)
    return question


def _form_values(data, question=None):
    """The form's text, type, choices and answer, refused when a question cannot be asked from them."""
    current = {"prompt": question.question_text, **screen_values(question)} if question else {}

    def field(name, default=""):
        return data.get(name, current.get(name, default))

    prompt = str(field("prompt") or "").strip()
    question_type = str(field("question_type") or "").strip()
    choices = field("choices", [])
    answer = str(field("correct_answer") or "").strip()
    if not prompt:
        raise QuestionError("Enter the question text.")
    if question_type not in SUPPORTED_TYPES:
        raise QuestionError("Question type must be true_false or multiple_choice.")
    if question_type == ExtractedQuestion.Type.TRUE_FALSE:
        choices, answer = ["True", "False"], answer.title()
    elif not isinstance(choices, list):
        raise QuestionError("Multiple-choice options must be a list.")
    else:
        choices = [str(choice).strip() for choice in choices if str(choice).strip()]

    missing = validation_issues(question_type, choices, answer)
    if missing:
        raise QuestionError(" ".join(missing), validation_issues=missing)
    return {"question_text": prompt, **bank_values(question_type, choices, answer)}


def _label(question):
    """Give the question its Bloom level, LOTS/HOTS and category."""
    labels = _get_classifier().classify(question.question_text)
    question.bloom_level = labels["bloom_level"]
    question.thinking_order = labels["thinking_order"] or ""
    question.category = labels["category"]


def _chosen_group(node, data):
    """The concept the teacher picked on the form, or None when they picked none."""
    raw = data.get("learning_object_group_id")
    if raw in (None, ""):
        return None
    try:
        return LearningObjectGroup.objects.get(pk=int(raw), outline_node=node)
    except (TypeError, ValueError, LearningObjectGroup.DoesNotExist):
        raise QuestionError("Selected concept was not found in this outline node.")


def _members(group):
    return list(
        group.learning_objects.filter(**CONFIRMED_OBJECTS).order_by("material_id", "order", "id")
    )


def _place(object_id, paired_by, concept="", check_result="", check_note=""):
    """Where a question goes and what the answer check found there."""
    return {
        "object_id": object_id, "paired_by": paired_by, "concept": concept,
        "check_result": check_result, "check_note": check_note,
    }


def _teacher_place(question, target, members, check=True):
    """The concept the teacher ruled on, with the answer read against that concept's text."""
    lead = next(
        (item for item in members if (item.content or "").strip() and _is_concept_source(item)),
        members[0],
    )
    concept = concept_name(lead)
    if not check:
        return _place(target.id, "teacher", concept)
    result = verify(question, {"concept": concept, "text": concept_source_text(lead)})
    return _place(target.id, "teacher", concept, result["verdict"], result["reason"])


def _chosen_place(question, group, check=True):
    """Where the question goes when the teacher picked its concept."""
    members = _members(group)
    if not members:
        raise QuestionError("The selected concept has no confirmed learning objects.")
    return _teacher_place(question, members[0], members, check)


def _confirmed_place(question, check=True):
    """Where the question goes when the teacher confirms the concept already suggested for it."""
    target = question.node
    members = (_members(target.group) if target.group_id else []) or [target]
    return _teacher_place(question, target, members, check)


def _found_place(question, index):
    """Where the question goes when nobody picked its concept: the first concept whose text supports the answer."""
    result = pair_question(question, index)
    chunk = result["concept"]
    concept = chunk["concept"] if chunk else ""
    if result["status"] == PAIRED:
        return _place(chunk["object_id"], "auto", concept, "supported")
    if result["status"] == KEY_CONFLICT:
        supported = result["supported_answer"]
        note = f"the text supports {supported}" if supported else "the text gives a different answer"
        return _place(chunk["object_id"], "", concept, "contradicted", note)
    if result["status"] == UNVERIFIED:
        return _place(None, "", check_result="unverified")
    return _place(None, "", check_result="unsupported")


def _apply(question, place):
    """Give the question its concept and the result of the check, then settle whether it is final."""
    place = place or _place(None, "")
    question.node_id = place["object_id"]
    question.paired_by = place["paired_by"]
    question.check_result = place["check_result"]
    question.check_note = place["check_note"]
    settle(question)


def _outcome(question, place, verb):
    """What happened to the question, in a sentence for the teacher."""
    problems = " ".join(issues(question))
    if structure_issues(question):
        return {"status": "held", "message": f"Question {verb}, but it still needs details. {problems}"}
    if place is None:
        return {"status": "held", "message": f"Question {verb}. It still has no concept."}
    if problems:
        return {"status": "held", "message": f"Question {verb} but held for review. {problems}"}
    return {"status": "paired", "message": f'Question {verb} and paired with "{place["concept"]}".'}


def _refuse_duplicate(node, question):
    duplicate = duplicate_in_topic(
        node, question_fingerprint(question.question_text), exclude_id=question.pk,
    )
    if duplicate is not None:
        raise QuestionError(
            "This question already exists in this topic.", status=409,
            duplicate_question_id=duplicate.id,
        )


def add_question(node, data):
    """Save a question the teacher typed; returns (question, outcome)."""
    question = Question(outline_node=node, source="manual", **_form_values(data))
    _refuse_duplicate(node, question)
    _label(question)
    group = _chosen_group(node, data)
    place = _chosen_place(question, group) if group else _found_place(question, build_index(node))
    _apply(question, place)
    return question, _outcome(question, place, "added")


def edit_question(node, question_id, data):
    """Save a teacher's edit of any question in the topic; returns (question, outcome)."""
    question = _question(node, question_id)
    for name, value in _form_values(data, question).items():
        setattr(question, name, value)
    _refuse_duplicate(node, question)
    _label(question)

    group = _chosen_group(node, data)
    if group is None and question.node_id and question.paired_by:
        group = question.node.group
    place = _chosen_place(question, group) if group else None

    question.outline_node = node
    if question.source != "manual":
        question.teacher_edited = True
    _apply(question, place)
    return question, _outcome(question, place, "updated")


def review_pairing(node, question_id, data):
    """Apply a teacher's ruling on a question's concept: confirm the suggested one, or choose another."""
    question = _question(node, question_id)
    decision = str(data.get("decision") or "").strip().casefold()
    if decision not in ("confirm", "change"):
        raise QuestionError("decision must be confirm or change.")
    broken = structure_issues(question)

    if decision == "change":
        group = _chosen_group(node, data)
        if group is None:
            raise QuestionError("Select a valid learning-object group.")
        place, verb = _chosen_place(question, group, check=not broken), "moved"
    elif question.node_id is None:
        raise QuestionError("No suggested question pairing is available to review.")
    else:
        place, verb = _confirmed_place(question, check=not broken), "confirmed"

    _apply(question, place)
    return question, _outcome(question, place, verb)


def keep_anyway(node, question_id):
    """The teacher keeps a question the answer check held: it becomes theirs and is given to learners."""
    question = _question(node, question_id)
    if not can_keep(question):
        raise QuestionError("Only a question held by the answer check, with its concept and details complete, can be kept.")
    question.paired_by = "teacher"
    question.check_result = "kept"
    settle(question)
    return question, {"status": "paired", "message": "Question kept. Learners will get it."}


def delete_question(node, question_id):
    """Delete a question from the bank."""
    _question(node, question_id).delete()


def label_printed(node):
    """Label the topic's printed questions that have no label yet and look for each one's concept; returns how many."""
    pending = list(
        topic_questions(node).filter(source="lesson_pdf", bloom_level="")
    )
    index = build_index(node) if pending else None
    for question in pending:
        _label(question)
        question.outline_node = node
        broken = structure_issues(question)
        if broken or question.node_id or question.paired_by:
            settle(question)
        else:
            _apply(question, _found_place(question, index))
    if pending:
        logger.info(
            "[Questions] topic %s  labelled %s printed question(s) and looked for each one's concept",
            node.id, len(pending),
        )
    return len(pending)
