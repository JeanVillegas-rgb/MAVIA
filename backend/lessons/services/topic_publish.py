"""Publish one topic, reporting what it is doing as it goes.

Publishing runs image narration, version settling and audio synthesis across
every confirmed material in a topic. Each of those calls a local model and can
take minutes, so the work is separated from the request that starts it: this
module does the work and emits progress, and the view owns only the threading.

Keeping it a plain function also keeps it testable -- the phases can be
exercised synchronously without a background thread or an HTTP round trip.
"""

from django.utils import timezone

from course.services import sync_course_outline
from course.version_assignment import settle_group

from ..models import LearningMaterial, LearningObject
from .audio_generator import AudioGenerationError, generate_material_audio_playlist, generate_version_audio
from .image_describer import populate_missing_image_descriptions
from .content_generator import build_narration_script_from_learning_objects, build_lesson_playlist


def _noop(event_type, message, **data):
    pass


def confirmed_materials_for(course, node):
    return (
        LearningMaterial.objects.filter(
            course=course,
            outline_node=node,
            generated_json__learning_objects_confirmed=True,
        )
        .exclude(learning_objects__isnull=True)
        .distinct()
    )


def run_topic_publish(course, node, set_confirmed, on_event=None):
    """Run every publish phase for one topic and return a summary.

    ``set_confirmed(material)`` re-runs the caller's confirmation bookkeeping
    after image narration adds content. It is injected rather than imported so
    this module does not depend on the viewset that owns that logic.
    """
    emit = on_event or _noop
    materials = list(confirmed_materials_for(course, node))
    if not materials:
        raise ValueError("Confirm at least one lesson file before publishing.")

    emit(
        "publish_started",
        f"Publishing {node.title}",
        node_id=node.id, material_count=len(materials),
    )

    image_generated = 0
    image_errors = []
    for index, material in enumerate(materials, start=1):
        emit(
            "image_descriptions_started",
            f"Describing images in {material.title}",
            index=index, total=len(materials), material_id=material.id,
        )
        result = populate_missing_image_descriptions(material)
        image_generated += result["generated_count"]
        image_errors.extend(result["errors"])
        if result["generated_count"]:
            set_confirmed(material)
        emit(
            "image_descriptions_finished",
            f"Described {result['generated_count']} image(s) in {material.title}",
            index=index, total=len(materials), material_id=material.id,
            generated=result["generated_count"],
        )

    # LessonVariant requires the student-facing lesson package wrapper.
    # Assignment normally happened at review; this is a backstop for content
    # edited afterwards. Settled groups generate nothing.
    sync_course_outline(course.id)
    groups = list(node.learning_object_groups.filter(learning_objects__material__in=materials).distinct())
    variant_generated = []
    variant_errors = []
    for index, group in enumerate(groups, start=1):
        emit(
            "versions_started",
            f"Settling versions for concept {index} of {len(groups)}",
            index=index, total=len(groups), group_id=group.id,
        )
        outcome = settle_group(group)
        variant_generated.extend(outcome["generated"])
        variant_errors.extend(outcome["errors"])
        emit(
            "versions_finished",
            f"Concept {index} of {len(groups)} settled",
            index=index, total=len(groups), group_id=group.id,
            generated=outcome["generated"],
        )

    # Checked in Python rather than with a queryset: "has both slots" needs two
    # independent joins on the same reverse relation, which a single exclude()
    # cannot express correctly.
    incomplete_versions = []
    for candidate in LearningObject.objects.filter(
        material__outline_node=node,
        material__in=materials,
        represented_by__isnull=True,
    ).prefetch_related("variants"):
        slots = {row.variant for row in candidate.variants.all() if row.narration.strip()}
        if not (candidate.content or "").strip() or not {"SIMPLIFIED", "ELABORATED"}.issubset(slots):
            incomplete_versions.append(candidate.id)
    if incomplete_versions:
        emit(
            "versions_incomplete",
            f"{len(incomplete_versions)} concept(s) still missing a version",
            learning_object_ids=incomplete_versions,
        )

    audio_generated = 0
    audio_errors = []
    for index, material in enumerate(materials, start=1):
        emit(
            "audio_started",
            f"Generating audio for {material.title}",
            index=index, total=len(materials), material_id=material.id,
        )
        try:
            active_objects = list(material.learning_objects.filter(represented_by__isnull=True).order_by("order", "id"))
            narration = build_narration_script_from_learning_objects([
                {"learning_object_id": item.id, "title": item.title, "content": item.content,
                 "type": "image_description" if item.kind == "image" else "teacher_text",
                 "section_title": item.section_title, "source_page": item.source_page}
                for item in active_objects
            ])
            material.generated_json = {**(material.generated_json or {}),
                "narration_script": narration, "lesson_playlist": build_lesson_playlist(narration)}
            material.save(update_fields=["generated_json"])
            result = generate_material_audio_playlist(material, scope="lessons") if active_objects else {"generated_count": 0}
            version_audio = generate_version_audio(material)
            result["generated_count"] += version_audio["generated_count"]
            audio_generated += result["generated_count"]
            emit(
                "audio_finished",
                f"Generated {result['generated_count']} track(s) for {material.title}",
                index=index, total=len(materials), material_id=material.id,
                generated=result["generated_count"],
            )
        except AudioGenerationError as exc:
            message = f"{material.title or material.pdf_file.name}: {exc}"
            audio_errors.append(message)
            emit(
                "audio_failed", message,
                index=index, total=len(materials), material_id=material.id,
            )

    ready = not (image_errors or variant_errors or incomplete_versions or audio_errors)
    node.published = ready
    node.published_at = timezone.now() if ready else None
    node.save(update_fields=["published", "published_at"])

    summary = {
        "published": ready,
        "published_at": node.published_at.isoformat() if node.published_at else None,
        "audio_generated_count": audio_generated,
        "materials_processed": len(materials),
        "audio_errors": audio_errors,
        "adaptive_variants_generated": len(variant_generated),
        "adaptive_variant_errors": variant_errors,
        "incomplete_versions": incomplete_versions,
        "image_descriptions_generated": image_generated,
        "image_description_errors": image_errors,
    }
    emit(
        "publish_finished" if ready else "publish_failed",
        f"{node.title} published" if ready else "Publishing incomplete. Resolve the reported content or audio errors and retry.",
        node_id=node.id, summary=summary,
    )
    return summary
