"""Whether a concept's question bank still matches the concept's text.

A concept's bank is written by the model from the whole concept -- every
PDF's telling of it -- and filed under one object, the lead of its Standard
version. When the concept's text changes (an object edited, deleted, or
another PDF's version joining or leaving), the bank may ask about text that
is no longer there. It is then out of date: the teacher keeps it or
regenerates it, and publishing waits until they do -- the same rule as an
out-of-date Simplified or Elaborated version.
"""

import hashlib

from django.db.models import Case, IntegerField, Value, When

from .pipeline import concept_source_text


def text_fingerprint(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _final_rows(owner):
    return owner.generated_questions.filter(status="final")


def bank_owner(group):
    """The object the concept's bank is filed under, if it has a bank."""
    from lessons.models import LearningObject

    return (
        LearningObject.objects.filter(group=group, generated_questions__status="final")
        .order_by("material_id", "order", "id")
        .first()
    )


def _all_confirmed(group):
    return not group.learning_objects.exclude(
        material__generated_json__learning_objects_confirmed=True,
    ).exists()


def bank_out_of_date(group):
    """True when the concept's text no longer matches its bank's.

    Not judged while any PDF in the concept is a draft: edits are worked out
    when it is confirmed again. A bank written before the text was recorded
    is not known to be stale and is not reported.
    """
    owner = bank_owner(group)
    if owner is None or not _all_confirmed(group):
        return False
    stored = set(_final_rows(owner).values_list("source_text_fingerprint", flat=True))
    if "" in stored:
        return False
    return stored != {text_fingerprint(concept_source_text(owner))}


def out_of_date_groups(outline_node):
    groups = outline_node.learning_object_groups.filter(
        learning_objects__generated_questions__status="final",
    ).distinct()
    return [group for group in groups if bank_out_of_date(group)]


def keep_bank(group):
    """The teacher's "Keep as is": the bank is current for today's text."""
    owner = bank_owner(group)
    if owner is None:
        return
    _final_rows(owner).update(
        source_text_fingerprint=text_fingerprint(concept_source_text(owner)),
    )


def refile_bank_before_delete(learning_object):
    """Keep the concept's bank when the object it is filed under is deleted.

    The bank belongs to the concept, not to this object; it was only filed
    here. It moves to another member now, so the deletion does not take it
    along, and ``settle_bank_owner`` files it under the concept's new lead
    once the object is gone. The concept's text has changed, so the bank
    reads as out of date. With no other member, the concept is gone and so
    is its bank: nothing is moved.
    """
    from lessons.models import LearningObject

    if learning_object.group_id is None or not learning_object.generated_questions.exists():
        return None
    target = (
        LearningObject.objects.filter(group_id=learning_object.group_id)
        .exclude(pk=learning_object.pk)
        # The same PDF's next object first: it is the likeliest new lead.
        .annotate(other_pdf=Case(
            When(material_id=learning_object.material_id, then=Value(0)),
            default=Value(1), output_field=IntegerField(),
        ))
        .order_by("other_pdf", "order", "id")
        .first()
    )
    if target is None:
        return None
    _move_bank(learning_object, target)
    return target.group


def _move_bank(source, target):
    from lessons.models import QuestionLearningObjectLink

    source.generated_questions.update(node=target)
    for link in QuestionLearningObjectLink.objects.filter(
        learning_object=source, method="generated_from_object",
    ):
        if QuestionLearningObjectLink.objects.filter(
            question_id=link.question_id, learning_object=target,
        ).exists():
            link.delete()
        else:
            link.learning_object = target
            link.save(update_fields=["learning_object"])


def settle_bank_owner(group):
    """File the concept's bank under its current Standard lead."""
    from course.version_assignment import group_original_id

    if group is None:
        return
    owner = bank_owner(group) or (
        group.learning_objects.filter(generated_questions__isnull=False).first()
    )
    if owner is None:
        return
    members = list(group.learning_objects.all())
    lead_id = group_original_id(group, members)
    lead = next((item for item in members if item.id == lead_id), None)
    if lead is not None and lead.id != owner.id:
        _move_bank(owner, lead)
