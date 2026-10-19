from .models import LessonVariant, bundle_segments, complete_generated_rows, standard_bundle_for
from .version_assignment import served_version_bundles


def _generated_versions(objects):
    """``{role: {"segments": [...], "origin": ...}}`` written for this bundle.

    Jean writes one generated explanation for the complete Standard bundle.
    It is stored on the bundle's lead object as MAVIA's compatibility anchor.
    """
    rows = [row for item in objects for row in item.variants.all()]
    return {
        slot: {
            "segments": [{"text": row.narration, "audio_url": row.audio_url} for row in complete],
            "origin": complete[0].origin,
        }
        for slot, complete in complete_generated_rows(objects, rows).items()
    }


def _version_from_segments(segments, *, origin):
    """A version's payload: its segments, and the same text joined.

    ``text`` is derived from the segments rather than read separately, so a
    caption can never drift from the wording the segment actually carries.
    """
    texts = [segment["text"].strip() for segment in segments]
    return {
        "text": "\n".join(text for text in texts if text),
        "audio_url": next(
            (segment["audio_url"] for segment in segments if segment["audio_url"]), ""
        ),
        "segments": segments,
        "origin": origin,
    }


def _build_chunk(learning_object):
    """One concept, served as its versions -- each an ordered bundle.

    The chunk is still keyed by the object that leads the concept, so existing
    readers keep working: ``text`` is the whole version joined, and
    ``segments`` is what it is actually made of, in document order.
    """
    variants = {}
    standard_objects = standard_bundle_for(learning_object)

    variants["standard"] = _version_from_segments(
        bundle_segments(standard_objects), origin="original"
    )

    for role, version in _generated_versions(standard_objects).items():
        variants[role.lower()] = _version_from_segments(
            version["segments"], origin=version["origin"]
        )

    # A version a PDF supplies is that PDF's own objects -- nothing is copied
    # into a row -- and it outranks anything generated for the same role.
    if learning_object.group_id is not None:
        for role, objects in served_version_bundles(learning_object.group).items():
            if role == "STANDARD":
                continue
            variants[role.lower()] = _version_from_segments(
                bundle_segments(objects), origin=LessonVariant.Origin.SOURCE_PDF
            )

    return {
        "id": learning_object.id,
        "metadata_id": str(learning_object.metadata_id),
        "order": learning_object.order,
        "title": learning_object.title,
        "variants": variants,
        "versions_complete": {"simplified", "elaborated"}.issubset(variants.keys()),
    }




