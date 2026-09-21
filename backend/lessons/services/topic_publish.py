"""Publish one topic, reporting what it is doing as it goes.

Publishing runs image narration, version settling and audio synthesis across
every confirmed material in a topic. Each of those calls a local model and can
take minutes, so the work is separated from the request that starts it: this
module does the work and emits progress, and the view owns only the threading.

Keeping it a plain function also keeps it testable -- the phases can be
exercised synchronously without a background thread or an HTTP round trip.
"""

from django.utils import timezone

from course.models import LessonVariant
from course.services import sync_course_outline
from course.version_assignment import settle_group

from ..models import LearningMaterial, LearningObject
from .audio_generator import (
    AudioGenerationError,
    generate_bundle_version_audio,
    generate_material_audio_playlist,
    generate_version_audio,
)
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


def concepts_missing_a_version(node, materials):
    """Lead objects of concepts a student could not be served both versions of.

    A concept is judged once, through its bundles, not once per object:

    * a role another PDF supplies is satisfied by that PDF's own objects --
      there is no ``LessonVariant`` row to look for, and demanding one would
      make every two-PDF topic unpublishable;
    * a role no bundle supplies must have been written, one row per object of
      the Normal bundle, so the version a student hears covers all of it;
    * only the Normal bundle's lead speaks for the concept. Every other object
      either repeats it or belongs to a bundle that is already a version --
      including a bundle still awaiting the teacher's confirmation, which is
      simply not a version yet and must not hold publishing back.

    ``materials`` is the publish's confirmed set, and a role only counts as
    supplied when the PDF supplying it is in that set: the audio phase runs
    over confirmed materials only, so un-confirming one PDF after grouping
    would otherwise publish a version with nothing to play.
    """
    from course.version_assignment import PRIMARY_SLOTS, version_bundles

    confirmed_ids = {material.id for material in materials}
    filled = {
        (learning_object_id, variant)
        for learning_object_id, variant, narration in LessonVariant.objects.filter(
            learning_object__material__in=materials,
        ).values_list("learning_object_id", "variant", "narration")
        if (narration or "").strip()
    }
    bundles_by_group = {}
    missing = []
    for candidate in (
        LearningObject.objects.filter(
            material__outline_node=node, material__in=materials,
        )
        .select_related("group")
        .order_by("order", "id")
    ):
        if candidate.group_id is None:
            if candidate.represented_by_id is not None:
                continue
            normal, supplied = [candidate], set()
        else:
            if candidate.group_id not in bundles_by_group:
                bundles_by_group[candidate.group_id] = version_bundles(candidate.group)
            bundles = bundles_by_group[candidate.group_id]
            normal = bundles.get("NORMAL") or []
            supplied = {
                role for role, objects in bundles.items()
                if role in PRIMARY_SLOTS
                and objects
                and all(item.material_id in confirmed_ids for item in objects)
            }
            if normal:
                if normal[0].id != candidate.id:
                    continue
            elif candidate.represented_by_id is None:
                # Nothing in the concept has text to teach; report it against
                # whichever object is still standing for it.
                normal = [candidate]
            else:
                continue
        if not any((item.content or "").strip() for item in normal):
            missing.append(candidate.id)
            continue
        if any(
            (item.id, slot) not in filled
            for slot in set(PRIMARY_SLOTS) - supplied
            for item in normal
        ):
            missing.append(candidate.id)
    return missing


def refresh_material_playlist(material):
    """Rewrite one material's narration script and lesson playlist.

    One entry per object this PDF still teaches in its own voice. An object
    ``represented_by`` another is that concept's version, not a track of this
    lesson, and is left out -- ``generate_version_audio`` speaks it instead.

    A concept taught as a bundle of three objects therefore keeps three
    tracks, in document order: this list is what the mobile package serves,
    so collapsing a concept here would collapse the lesson a learner hears.

    Returns the objects it wrote tracks for.
    """
    active_objects = list(
        material.learning_objects.filter(represented_by__isnull=True).order_by("order", "id")
    )
    narration = build_narration_script_from_learning_objects([
        {"learning_object_id": item.id, "title": item.title, "content": item.content,
         "type": "image_description" if item.kind == "image" else "teacher_text",
         "section_title": item.section_title, "source_page": item.source_page}
        for item in active_objects
    ])
    material.generated_json = {**(material.generated_json or {}),
        "narration_script": narration, "lesson_playlist": build_lesson_playlist(narration)}
    material.save(update_fields=["generated_json"])
    return active_objects


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
        # One named event per problem, so the teacher reads which concept needs
        # attention instead of a generic "resolve the reported errors".
        for error in outcome["errors"]:
            concept = LearningObject.objects.filter(pk=error.get("learning_object_id")).first()
            name = concept.title if concept else (group.label or f"Concept {index}")
            emit(
                "versions_failed",
                f"“{name}”: {error.get('detail') or 'versions could not be settled.'}",
                group_id=group.id,
                learning_object_id=error.get("learning_object_id"),
                slots=error.get("slots", []),
            )
        emit(
            "versions_finished",
            f"Concept {index} of {len(groups)} settled",
            index=index, total=len(groups), group_id=group.id,
            generated=outcome["generated"],
        )

    incomplete_versions = concepts_missing_a_version(node, materials)
    if incomplete_versions:
        names = list(
            LearningObject.objects.filter(pk__in=incomplete_versions).values_list("title", flat=True)
        )
        emit(
            "versions_incomplete_failed",
            f"{len(incomplete_versions)} concept(s) still missing a version: {', '.join(names)}",
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
            active_objects = refresh_material_playlist(material)
            result = generate_material_audio_playlist(material, scope="lessons") if active_objects else {"generated_count": 0}
            version_audio = generate_version_audio(material)
            result["generated_count"] += version_audio["generated_count"]
            # A version this PDF supplies is its own objects, which the lesson
            # playlist above leaves out because they are represented. Without
            # their own clips the alternate track would play silence.
            bundle_audio = generate_bundle_version_audio(material)
            result["generated_count"] += bundle_audio["generated_count"]
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

    # The learning path is saved only when everything else succeeded, so the
    # saved path always matches what students can see. A failure here keeps the
    # topic unpublished rather than publishing content without a path.
    path_summary = None
    path_error = ""
    if ready:
        from learning_path.services.publishing import publish_learning_path

        emit("path_started", "Building the learning path", node_id=node.id)
        try:
            path_summary = publish_learning_path(node)
        except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
            path_error = f"The learning path could not be built: {exc}"
            ready = False
            emit("path_failed", path_error, node_id=node.id)
        else:
            emit(
                "path_finished",
                f"Learning path saved: {path_summary['steps']} steps, "
                f"{path_summary['links']} prerequisite links",
                node_id=node.id,
                steps=path_summary["steps"],
                links=path_summary["links"],
                moved=path_summary["moved"],
            )

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
        "learning_path": path_summary,
        "learning_path_error": path_error,
    }
    emit(
        "publish_finished" if ready else "publish_failed",
        f"{node.title} published" if ready else "Publishing incomplete. Resolve the reported content or audio errors and retry.",
        node_id=node.id, summary=summary,
    )
    return summary
