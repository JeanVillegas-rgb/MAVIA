"""Rewrite stored figure narrations with the current description prompt.

The prompt was rewritten as ROLE / TASK / CONTEXT / FORMAT on 2026-09-21, with
a no-preamble rule and an instruction not to describe the visual layout. The
descriptions already in the database predate it, and they cost more than they
look: they are read aloud to a learner, they name their concept, and their
wording feeds the learning path's key terms. One live consequence was the
forbidden `Solid -> Gas` edge on topic 152, which rested entirely on "drawn",
"spaced", "dots" and "compress" -- the old prompt's rendering vocabulary,
shared by two unrelated diagrams.

`populate_missing_image_descriptions` only fills *blank* narrations, so it
cannot be used for this: nothing here is blank. This command re-runs the
description for figures that already have one.

A narration a teacher edited is not detectable from the row, so the command
refuses to run without `--topic` or `--material`: rewriting every figure in the
database by accident is not recoverable from the rows themselves. Take a
database copy first; `--dry-run` shows what would be rewritten.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from lessons.models import LearningMaterial, LearningObject
from lessons.services.content_generator import _title_from_teacher_text
from lessons.services.image_describer import (
    _MAX_NEARBY_TEXT,
    _learning_object_image_bytes,
    describe_image_for_lesson,
)


def retitle(learning_object, old_description, new_description):
    """The figure's new title, when the old one came from the old description.

    A figure with no caption in the PDF is titled from the first sentence of
    its description, so rewriting the description leaves that title describing
    text that no longer exists -- and the title is what names the concept and
    is read aloud. It is only safe to re-derive when the stored title is
    *exactly* what the old description would have produced: anything else is a
    caption the PDF supplied or a name a teacher chose, and neither may be
    overwritten.
    """
    stored = (learning_object.title or "").strip()
    if not stored or stored != _title_from_teacher_text(old_description).strip():
        return None
    derived = _title_from_teacher_text(new_description).strip()
    return derived if derived and derived != stored else None


class Command(BaseCommand):
    help = "Rewrite figure narrations with the current ROLE/TASK/CONTEXT/FORMAT prompt."

    def add_arguments(self, parser):
        parser.add_argument("--topic", type=int, action="append", default=[],
                            help="Outline node id. Repeatable.")
        parser.add_argument("--material", type=int, action="append", default=[],
                            help="Learning material id. Repeatable.")
        parser.add_argument("--dry-run", action="store_true",
                            help="List the figures that would be rewritten, and stop.")

    def handle(self, *args, **options):
        topics, materials = options["topic"], options["material"]
        if not topics and not materials:
            raise CommandError(
                "Give --topic or --material. Refusing to rewrite every figure in the "
                "database: the old narration is not recoverable from the row."
            )

        queryset = LearningObject.objects.filter(kind=LearningObject.Kind.IMAGE)
        if topics:
            queryset = queryset.filter(material__outline_node_id__in=topics)
        if materials:
            queryset = queryset.filter(material_id__in=materials)
        figures = list(queryset.select_related("material").order_by("material_id", "order", "id"))

        if not figures:
            self.stdout.write("No figures matched.")
            return

        self.stdout.write(f"{len(figures)} figure(s) to rewrite.")
        if options["dry_run"]:
            for item in figures:
                words = len((item.content or "").split())
                self.stdout.write(f"  obj {item.id}  {words:3} words  {item.title[:60]!r}")
            self.stdout.write(self.style.WARNING("Dry run: nothing written."))
            return

        rewritten, retitled, unchanged, errors = [], [], [], []
        for item in figures:
            material = item.material
            try:
                image_bytes = _learning_object_image_bytes(item)
            except OSError as exc:
                errors.append((item.id, str(exc)))
                continue
            if not image_bytes:
                errors.append((item.id, "the saved image file is unavailable"))
                continue

            description = describe_image_for_lesson(
                image_bytes,
                lesson_title=(
                    material.outline_node.title if material.outline_node_id else material.title
                ),
                nearby_text=(material.extracted_text or "")[:_MAX_NEARBY_TEXT],
                caption=item.title,
            )
            if not description:
                errors.append((item.id, "the model returned no narration"))
                continue
            # A narration that came back identical is left alone rather than
            # saved, so the row's timestamps still mean something.
            if description.strip() == (item.content or "").strip():
                unchanged.append(item.id)
                continue

            before = item.content or ""
            new_title = retitle(item, before, description)
            fields = ["content"]
            with transaction.atomic():
                item.content = description
                if new_title:
                    item.title = new_title[:255]
                    fields.append("title")
                item.save(update_fields=fields)
            rewritten.append((item.id, before, description))
            self.stdout.write(
                f"  obj {item.id}: {len(before.split())} -> {len(description.split())} words"
            )
            if new_title:
                retitled.append(item.id)
                self.stdout.write(f"      retitled: {new_title[:70]!r}")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Rewritten: {len(rewritten)}"))
        if unchanged:
            self.stdout.write(f"Unchanged (model returned the same text): {unchanged}")
        for object_id, detail in errors:
            self.stdout.write(self.style.ERROR(f"obj {object_id}: {detail}"))

        if retitled:
            self.stdout.write(f"Retitled (their title came from the old description): {retitled}")
        if rewritten:
            self.stdout.write("")
            self.stdout.write(
                "Concept names follow their object's title, so run "
                "repair_bundle_labels next, then republish the topic."
            )
