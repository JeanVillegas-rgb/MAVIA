"""What a teacher does to a question on the Questions screen: add, edit, pair to a concept, delete."""

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from lessons.models import (
    LearningMaterial,
    LearningObjectGroup,
    Question,
    QuestionLearningObjectLink,
)
from lessons.services.question_workflow import (
    duplicate_for_topic,
    enriched_question_values,
    question_snapshots,
    sync_question_to_adaptive,
    validation_issues,
)
from question_generation.models import GeneratedQuestion

from .grounding import build_index, verify
from .pairing import KEY_CONFLICT, PAIRED, UNVERIFIED, pair_question
from .pipeline import _is_concept_source, concept_name, concept_source_text

Status = QuestionLearningObjectLink.ReviewStatus
SUPPORTED_TYPES = (Question.Type.TRUE_FALSE, Question.Type.MULTIPLE_CHOICE)
CONFIRMED = (Status.AUTO_CONFIRMED, Status.TEACHER_CONFIRMED)
CONFIRMED_OBJECTS = {"material__generated_json__learning_objects_confirmed": True}
CHECKED_FIELDS = ("prompt", "question_type", "choices", "correct_answer")

CHECKED_METHOD = "answer_check"
CHOSEN_METHOD = "teacher_selected"
MANUAL_SOURCE = "manual_questions"

NOT_CHECKED = "The answer could not be checked right now. Edit and save the question again to retry."
NO_CONCEPT = "No concept's text supports this answer. Choose a concept or revise the question."


class QuestionError(ValueError):
    """A request the teacher has to correct, with the HTTP status to answer with."""

    def __init__(self, detail, status=400, **extra):
        super().__init__(detail)
        self.detail = detail
        self.status = status
        self.extra = extra


def _question(node, question_id):
    try:
        return Question.objects.select_related("material").get(
            pk=question_id, material__outline_node=node,
        )
    except (Question.DoesNotExist, ValueError):
        raise QuestionError("Question was not found in this outline node.", status=404)


def _form_values(data, question=None):
    """The form's text, type, choices and answer, labelled and ready to save."""
    def field(name, default=""):
        return data.get(name, getattr(question, name, default) if question else default)

    prompt = str(field("prompt") or "").strip()
    question_type = str(field("question_type") or "").strip()
    choices = field("choices", [])
    answer = str(field("correct_answer") or "").strip()
    if not prompt:
        raise QuestionError("Enter the question text.")
    if question_type not in SUPPORTED_TYPES:
        raise QuestionError("Question type must be true_false or multiple_choice.")
    if question_type == Question.Type.TRUE_FALSE:
        choices, answer = ["True", "False"], answer.title()
    elif not isinstance(choices, list):
        raise QuestionError("Multiple-choice options must be a list.")
    else:
        choices = [str(choice).strip() for choice in choices if str(choice).strip()]

    values = enriched_question_values(prompt, question_type, choices, answer)
    if values["validation_issues"]:
        raise QuestionError(
            " ".join(values["validation_issues"]),
            validation_issues=values["validation_issues"],
        )
    return values


def _chosen_group(node, data):
    """The concept the teacher picked on the form, or None when they picked none."""
    raw = data.get("learning_object_group_id")
    if raw in (None, ""):
        return None
    try:
        return LearningObjectGroup.objects.get(pk=int(raw), outline_node=node)
    except (TypeError, ValueError, LearningObjectGroup.DoesNotExist):
        raise QuestionError("Selected concept was not found in this outline node.")


def _draft(values):
    """The question in the shape the checker reads: lettered choices and a letter for the answer."""
    if values["question_type"] == Question.Type.TRUE_FALSE:
        return GeneratedQuestion(
            question_text=values["prompt"], question_format="TF",
            correct_answer=values["correct_answer"],
        )
    choices = {chr(65 + index): choice for index, choice in enumerate(values["choices"])}
    letter = next(
        (key for key, choice in choices.items() if choice == values["correct_answer"]), "",
    )
    return GeneratedQuestion(
        question_text=values["prompt"], question_format="MCQ",
        choices=choices, correct_answer=letter,
    )


def _check(values, members, check=True):
    """(concept name, what is wrong) after reading the answer against the concept members belong to."""
    lead = next(
        (item for item in members if (item.content or "").strip() and _is_concept_source(item)),
        members[0],
    )
    concept = concept_name(lead)
    if not check:
        return concept, ""
    result = verify(_draft(values), {"concept": concept, "text": concept_source_text(lead)})
    if result["verdict"] == "supported":
        return concept, ""
    if result["verdict"] == "unverified":
        return concept, NOT_CHECKED
    return concept, f'"{concept}" does not support this answer: {result["reason"]}'


def _chosen_place(values, group, check=True):
    """Where the question goes when the teacher picked its concept."""
    members = list(
        group.learning_objects.filter(**CONFIRMED_OBJECTS).order_by("material_id", "order", "id")
    )
    if not members:
        raise QuestionError("The selected concept has no confirmed learning objects.")
    concept, issue = _check(values, members, check)
    return {
        "object_id": members[0].id, "review_status": Status.TEACHER_CONFIRMED,
        "method": CHOSEN_METHOD, "is_primary": True, "score": 0.0,
        "concept": concept, "issue": issue,
    }


def _confirmed_place(values, link, check=True):
    """Where the question goes when the teacher confirms the concept already suggested for it."""
    target = link.learning_object
    members = [target]
    if target.group_id:
        members = list(
            target.group.learning_objects.filter(**CONFIRMED_OBJECTS)
            .order_by("material_id", "order", "id")
        ) or [target]
    concept, issue = _check(values, members, check)
    return {
        "object_id": target.id, "review_status": Status.TEACHER_CONFIRMED,
        "method": link.method, "is_primary": True, "score": link.relevance_score,
        "concept": concept, "issue": issue,
    }


def _found_place(values, node):
    """Where the question goes when the teacher picked no concept: the first concept whose text supports the answer."""
    result = pair_question(_draft(values), build_index(node))
    chunk = result["concept"]
    place = {
        "object_id": None, "review_status": "", "method": CHECKED_METHOD, "is_primary": False,
        "score": 0.0, "concept": chunk["concept"] if chunk else "", "issue": "",
    }
    if result["status"] == PAIRED:
        place.update(
            object_id=chunk["object_id"], review_status=Status.AUTO_CONFIRMED, is_primary=True,
            score=result["tried"][-1]["score"] or 0.0,
        )
    elif result["status"] == KEY_CONFLICT:
        place.update(
            object_id=chunk["object_id"], review_status=Status.PENDING_REVIEW,
            issue=(
                f'"{chunk["concept"]}" supports a different answer'
                + (f': {result["supported_answer"]}' if result["supported_answer"] else ".")
            ),
        )
    elif result["status"] == UNVERIFIED:
        place["issue"] = NOT_CHECKED
    else:
        place["issue"] = NO_CONCEPT
    return place


def _snapshot(material):
    stored = material.generated_json or {}
    stored["questions"] = question_snapshots(material)
    material.generated_json = stored
    material.save(update_fields=["generated_json"])


def _settle(question, issues):
    """Record what is still wrong with the question, then give it to or take it from learners."""
    question.validation_issues = issues
    question.validation_status = (
        Question.ValidationStatus.NEEDS_REVIEW if issues else Question.ValidationStatus.READY
    )
    question.save(update_fields=["validation_issues", "validation_status"])
    sync_question_to_adaptive(question)
    _snapshot(question.material)


def _apply(question, place, broken=()):
    """Replace the question's pairing with place and record what the check found."""
    question.learning_object_links.all().delete()
    if place and place["object_id"]:
        teacher = place["review_status"] == Status.TEACHER_CONFIRMED
        QuestionLearningObjectLink.objects.create(
            question=question,
            learning_object_id=place["object_id"],
            relevance_score=round(float(place["score"]), 6),
            method=place["method"],
            is_primary=place["is_primary"],
            review_status=place["review_status"],
            reviewed_at=timezone.now() if teacher else None,
        )
    issue = place["issue"] if place else ""
    _settle(question, list(broken) or ([issue] if issue else []))


def _outcome(place, verb, broken=()):
    """What happened to the question, in a sentence for the teacher."""
    if broken:
        return {
            "status": "held",
            "message": f"Question {verb}, but it still needs details. {' '.join(broken)}",
        }
    if place is None:
        return {"status": "unpaired", "message": f"Question {verb} with no concept assigned."}
    if not place["issue"]:
        concept = f'"{place["concept"]}"'
        return {"status": "paired", "message": f"Question {verb} and paired with {concept}."}
    return {
        "status": "held",
        "message": f"Question {verb} but held for review. {place['issue']}",
    }


def _manual_material(node):
    """The topic's holder for typed questions, created on first use."""
    material = LearningMaterial.objects.filter(
        course=node.course, outline_node=node,
        generated_json__document_source=MANUAL_SOURCE,
    ).first()
    if material is not None:
        return material
    module = node
    while module.parent_id:
        module = module.parent
    return LearningMaterial.objects.create(
        course=node.course, outline_node=node, module_node=module,
        title="Manually added questions", pdf_file="",
        status=LearningMaterial.Status.COMPLETED,
        generated_json={
            "document_role": "assessment",
            "document_source": MANUAL_SOURCE,
            "learning_objects": [],
        },
    )


def _refuse_duplicate(node, fingerprint, exclude_id=None):
    duplicate = duplicate_for_topic(node.course_id, node.id, fingerprint, exclude_id=exclude_id)
    if duplicate is not None:
        raise QuestionError(
            "This question already exists in this topic.", status=409,
            duplicate_question_id=duplicate.id,
        )


def add_question(node, data):
    """Save a question the teacher typed; returns (question, outcome)."""
    values = _form_values(data)
    _refuse_duplicate(node, values["content_fingerprint"])
    group = _chosen_group(node, data)
    place = _chosen_place(values, group) if group else _found_place(values, node)

    with transaction.atomic():
        material = _manual_material(node)
        order = (material.questions.aggregate(Max("order"))["order__max"] or -1) + 1
        question = Question.objects.create(
            material=material, source_type=Question.SourceType.MANUAL, order=order,
            source_excerpt="Manually added by the teacher.", **values,
        )
        _apply(question, place)
    return question, _outcome(place, "added")


def edit_question(node, question_id, data):
    """Save a teacher's edit of any question in the topic; returns (question, outcome)."""
    question = _question(node, question_id)
    values = _form_values(data, question)
    _refuse_duplicate(node, values["content_fingerprint"], exclude_id=question.id)

    if "learning_object_group_id" in data:
        group = _chosen_group(node, data)
    else:
        link = question.learning_object_links.filter(
            review_status__in=CONFIRMED,
        ).select_related("learning_object__group").first()
        group = link.learning_object.group if link else None
    place = _chosen_place(values, group) if group else None

    with transaction.atomic():
        for name, value in values.items():
            setattr(question, name, value)
        fields = list(values)
        if question.source_type != Question.SourceType.MANUAL:
            question.teacher_edited = True
            fields.append("teacher_edited")
        question.save(update_fields=fields)
        _apply(question, place)
    return question, _outcome(place, "updated")


def review_pairing(node, question_id, data):
    """Apply a teacher's ruling on a question's concept: confirm the suggestion, change it, or leave it unpaired."""
    question = _question(node, question_id)
    decision = str(data.get("decision") or "").strip().casefold()
    if decision not in ("confirm", "change", "unpair"):
        raise QuestionError("decision must be confirm, change, or unpair.")

    link = (
        question.learning_object_links
        .filter(**{f"learning_object__{key}": value for key, value in CONFIRMED_OBJECTS.items()})
        .select_related("learning_object__group")
        .order_by("-is_primary", "-relevance_score", "id")
        .first()
    )
    values = {name: getattr(question, name) for name in CHECKED_FIELDS}
    broken = validation_issues(question.question_type, question.choices, question.correct_answer)

    if decision == "change":
        group = _chosen_group(node, data)
        if group is None:
            raise QuestionError("Select a valid learning-object group.")
        place, verb = _chosen_place(values, group, check=not broken), "moved"
    elif link is None:
        raise QuestionError("No suggested question pairing is available to review.")
    elif decision == "confirm":
        place, verb = _confirmed_place(values, link, check=not broken), "confirmed"
    else:
        with transaction.atomic():
            question.learning_object_links.exclude(pk=link.pk).delete()
            link.is_primary = False
            link.review_status = Status.TEACHER_UNPAIRED
            link.reviewed_at = timezone.now()
            link.save(update_fields=["is_primary", "review_status", "reviewed_at"])
            _settle(question, broken)
        return question, {"status": "unpaired", "message": "Question left unpaired."}

    with transaction.atomic():
        _apply(question, place, broken)
    return question, _outcome(place, verb, broken)


def delete_question(node, question_id):
    """Delete a question, its pairing and the copy learners get."""
    question = _question(node, question_id)
    material, copy_id = question.material, question.adaptive_question_id
    with transaction.atomic():
        question.delete()
        if copy_id:
            GeneratedQuestion.objects.filter(pk=copy_id).delete()
        _snapshot(material)
