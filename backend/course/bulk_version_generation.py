"""Classify existing PDF source variants for every concept in one topic."""

from .version_assignment import assign_group_versions
from .models import LessonVariant


def _noop(event_type, message, **data):
    pass


def classify_all_source_versions(outline_node, on_event=None):
    """Classify PDF sources without generating any missing adaptive wording."""
    emit = on_event or _noop
    groups = list(outline_node.learning_object_groups.all().order_by("id"))
    connected_group_ids = {
        group.id
        for group in groups
        if group.learning_objects.exclude(content="").count() > 1
    }
    emit(
        "versions_classification_started",
        f"Classifying sources for {len(connected_group_ids)} concept(s)",
        total=len(connected_group_ids),
    )
    representatives = []
    seen = set()
    errors = []
    classified_groups = 0
    classification_index = 0
    for group in groups:
        is_connected = group.id in connected_group_ids
        if is_connected:
            classification_index += 1
            emit(
                "version_classification_started",
                f"Classifying {group.label or f'concept {group.id}'}",
                index=classification_index,
                total=len(connected_group_ids),
                group_id=group.id,
            )
        state = assign_group_versions(group, use_llm=True)
        representative_id = state["representative_id"]
        if state.get("classification_error"):
            error = {"group_id": group.id, "detail": state["classification_error"]}
            errors.append(error)
            emit(
                "version_classification_failed",
                f"Could not classify {group.label or f'concept {group.id}'}",
                index=classification_index,
                total=len(connected_group_ids),
                group_id=group.id,
                errors=[error],
            )
        elif is_connected:
            classified_groups += 1
            emit(
                "version_classification_finished",
                f"Classified {group.label or f'concept {group.id}'}",
                index=classification_index,
                total=len(connected_group_ids),
                group_id=group.id,
                assigned=len(state["assigned"]),
                extras=state["extras"],
            )
        if (
            representative_id is None
            or representative_id in seen
            or not state.get("original_selected")
        ):
            continue
        representative = group.learning_objects.filter(pk=representative_id).first()
        if representative is None or not (representative.content or "").strip():
            continue
        seen.add(representative_id)
        representatives.append(representative)

    summary = {
        "concept_count": len(representatives),
        "grouped_concept_count": len(connected_group_ids),
        "classified_group_count": classified_groups,
        "source_variant_count": LessonVariant.objects.filter(
            learning_object__group__outline_node=outline_node,
            origin=LessonVariant.Origin.SOURCE_PDF,
        ).count(),
        "extra_count": LessonVariant.objects.filter(
            learning_object__group__outline_node=outline_node,
            origin=LessonVariant.Origin.SOURCE_PDF,
            variant="EXTRA",
        ).count(),
        "generated_count": 0,
        "skipped_count": 0,
        "errors": errors,
    }
    emit("versions_bulk_finished", "PDF source classification finished", summary=summary)
    return summary
