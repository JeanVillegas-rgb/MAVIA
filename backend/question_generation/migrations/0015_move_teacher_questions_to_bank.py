import hashlib
import re

from django.db import migrations

PAIRED_BY = {
    "auto_confirmed": "auto",
    "teacher_confirmed": "teacher",
    "teacher_unpaired": "teacher",
}
KEEPS_CONCEPT = ("auto_confirmed", "teacher_confirmed", "pending_review")
SOURCE = {"pdf": "lesson_pdf", "generated": "generated"}
FORMAT = {"multiple_choice": "MCQ", "true_false": "TF"}


def fingerprint(text):
    value = re.sub(r"^\s*(?:question\s*)?\d+\s*[.)-]\s*", "", text or "", flags=re.I)
    value = re.sub(r"[^a-z0-9\s]", " ", value.casefold())
    value = re.sub(r"\s+", " ", value).strip()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def bank_shape(question):
    question_format = FORMAT.get(question.question_type, "")
    if question_format == "TF":
        return question_format, None, (question.correct_answer or "").capitalize()
    choices = question.choices if isinstance(question.choices, list) else []
    lettered = {chr(65 + index): choice for index, choice in enumerate(choices)}
    letter = next(
        (key for key, choice in lettered.items() if choice == question.correct_answer), "",
    )
    return question_format, lettered or None, letter


def move_to_bank(apps, schema_editor):
    """Give every teacher-facing question one row in the bank, carrying its concept and review state."""
    Listed = apps.get_model("lessons", "Question")
    Link = apps.get_model("lessons", "QuestionLearningObjectLink")
    Material = apps.get_model("lessons", "LearningMaterial")
    Bank = apps.get_model("question_generation", "Question")

    for listed in Listed.objects.select_related("material").order_by("id"):
        link = (
            Link.objects.filter(question=listed)
            .order_by("-is_primary", "-relevance_score", "id")
            .first()
        )
        review = link.review_status if link else ""
        copy = Bank.objects.filter(pk=listed.adaptive_question_id).first()
        mirror = Bank.objects.filter(extracted_question=listed).first()
        if copy is not None and mirror is not None and mirror.pk != copy.pk:
            mirror.delete()
        row = copy or mirror or Bank()

        row.question_text = listed.prompt
        row.question_format, row.choices, row.correct_answer = bank_shape(listed)
        row.bloom_level = listed.bloom_level
        row.thinking_order = listed.thinking_order
        row.category = listed.category
        row.outline_node_id = listed.material.outline_node_id
        row.node_id = link.learning_object_id if review in KEEPS_CONCEPT else None
        row.paired_by = PAIRED_BY.get(review, "")
        row.source = SOURCE.get(listed.source_type, "manual")
        row.extracted_question_id = listed.id if listed.source_type == "pdf" else None
        row.teacher_edited = listed.teacher_edited
        row.validation_issues = listed.validation_issues or []
        row.content_fingerprint = listed.content_fingerprint or fingerprint(listed.prompt)
        ready = (
            row.node_id
            and row.paired_by
            and not row.validation_issues
            and row.thinking_order in ("LOT", "HOT")
        )
        row.status = "final" if ready else "review"
        row.save()

    for row in Bank.objects.filter(node__isnull=False).select_related("node__material"):
        changed = []
        if row.outline_node_id is None:
            row.outline_node_id = row.node.material.outline_node_id
            changed.append("outline_node")
        if row.source == "generated" and row.status == "final" and not row.paired_by:
            row.paired_by = "auto"
            changed.append("paired_by")
        if not row.content_fingerprint:
            row.content_fingerprint = fingerprint(row.question_text)
            changed.append("content_fingerprint")
        if changed:
            row.save(update_fields=changed)

    Listed.objects.exclude(source_type="pdf").delete()
    Material.objects.filter(generated_json__document_source="manual_questions").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0026_question_adaptive_model_rename"),
        ("question_generation", "0014_remove_question_generation_fingerprint"),
    ]

    operations = [
        migrations.RunPython(move_to_bank, migrations.RunPython.noop),
    ]
