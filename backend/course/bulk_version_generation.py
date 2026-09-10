"""Generate missing adaptive text versions for every concept in one topic."""

from .variant_generator import fill_missing_slots
from .version_assignment import assign_group_versions


def _noop(event_type, message, **data):
    pass


def generate_all_missing_versions(outline_node, on_event=None):
    """Fill only empty Simplified/Elaborated slots and report progress."""
    emit = on_event or _noop
    groups = list(outline_node.learning_object_groups.all().order_by("id"))
    representatives = []
    seen = set()
    for group in groups:
        state = assign_group_versions(group)
        representative_id = state["representative_id"]
        if representative_id is None or representative_id in seen:
            continue
        representative = group.learning_objects.filter(pk=representative_id).first()
        if representative is None or not (representative.content or "").strip():
            continue
        seen.add(representative_id)
        representatives.append(representative)

    total = len(representatives)
    emit(
        "versions_bulk_started",
        f"Generating missing versions for {total} concept(s)",
        total=total,
    )

    generated = 0
    skipped = 0
    errors = []
    for index, representative in enumerate(representatives, start=1):
        emit(
            "version_started",
            f"Checking {representative.title}",
            index=index,
            total=total,
            learning_object_id=representative.id,
        )
        result = fill_missing_slots(representative)
        generated += len(result["generated"])
        skipped += len(result["skipped"])
        if result["errors"]:
            for error in result["errors"]:
                errors.append({"learning_object_id": representative.id, **error})
            emit(
                "version_failed",
                f"Could not finish versions for {representative.title}",
                index=index,
                total=total,
                learning_object_id=representative.id,
                errors=result["errors"],
            )
        else:
            emit(
                "version_finished",
                f"Finished {representative.title}",
                index=index,
                total=total,
                learning_object_id=representative.id,
                generated=result["generated"],
                skipped=result["skipped"],
            )

    summary = {
        "concept_count": total,
        "generated_count": generated,
        "skipped_count": skipped,
        "errors": errors,
    }
    emit("versions_bulk_finished", "Version generation finished", summary=summary)
    return summary
