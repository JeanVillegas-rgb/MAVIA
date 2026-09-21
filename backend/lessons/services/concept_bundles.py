"""A concept's objects from one PDF, in document order.

Two PDFs chunk the same lesson at different grain: one teaches Solid in a
single passage, the other as a section plus a diagram plus examples. A concept
therefore holds a *bundle* per PDF rather than a single object. Nothing is
stored: a bundle is derived here, and every consumer -- version assignment,
audio, questions, the learning path, the API -- reads it through this module so
their ordering can never drift apart.
"""

from collections import defaultdict


def material_order(node):
    """The topic's material ids, oldest upload first."""
    return list(
        node.materials.order_by("created_at", "id").values_list("id", flat=True)
    )


def bundles_for_group(group):
    """``{material_id: [objects in document order]}`` for one concept.

    A caller that already prefetched ``learning_objects`` (with or without
    their materials) gets its cache used rather than re-queried: asking for
    ``select_related("material")`` unconditionally issued a fresh query per
    concept and quietly undid the prefetch.
    """
    prefetched = "learning_objects" in getattr(group, "_prefetched_objects_cache", {})
    manager = group.learning_objects
    items = manager.all() if prefetched else manager.select_related("material").all()
    bundles = defaultdict(list)
    for item in items:
        bundles[item.material_id].append(item)
    for objects in bundles.values():
        objects.sort(key=lambda item: (item.order, item.id))
    return dict(bundles)


def ordered_members(group, ranked=None):
    """Every member: materials in upload order, objects in document order.

    ``ranked`` is ``{material_id: upload rank}`` for the topic. A caller
    looping over a topic's concepts already knows it and should pass it:
    deriving it here costs one query for ``group.outline_node`` and another
    for the material list, per concept.
    """
    bundles = bundles_for_group(group)
    if ranked is None:
        ranked = {
            material_id: rank
            for rank, material_id in enumerate(material_order(group.outline_node))
        }
    members = []
    for material_id in sorted(bundles, key=lambda mid: (ranked.get(mid, len(ranked)), mid)):
        members.extend(bundles[material_id])
    return members


def bundle_text(objects):
    """The bundle's text: each object's content, in order."""
    return "\n".join(
        (item.content or "").strip() for item in objects if (item.content or "").strip()
    )


def bundle_lead(objects):
    """The object that speaks for the bundle -- its first."""
    return objects[0] if objects else None


def bundle_heading(objects):
    """What the bundle is called: its first heading, else its first title.

    Taken from the heading rather than the first object because a bundle often
    opens with a figure that has no heading of its own; naming the concept
    after that figure cost the learning path its edges when merging did it.
    """
    for item in objects:
        if (item.section_title or "").strip():
            return item.section_title.strip()
    return (objects[0].title or "").strip() if objects else ""


def bundle_label(objects):
    """What an automatic concept label takes from a bundle (design §3.5).

    The heading names the concept only when the bundle holds more than one
    object; a single-object bundle keeps that object's own title. Measured on
    the real upload: Solid, Liquid and Gas each sit alone under the heading
    "Matter", so naming them after their heading gave three concepts the same
    name and the learning-path criteria's same-name veto then deleted their
    edges. A multi-object bundle still needs the heading, because it often
    opens with a figure whose own title names nothing.
    """
    if not objects:
        return ""
    if len(objects) > 1:
        return bundle_heading(objects)
    return (objects[0].title or "").strip()
