"""A topic's grouped learning objects as ordered concepts."""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from course.version_assignment import assign_group_versions
from lessons.services.concept_bundles import ordered_members


@dataclass(frozen=True)
class Concept:
    """A LearningObjectGroup with a learning object's fields."""

    id: int
    title: str
    content: str
    section_title: str
    kind: str
    order: int
    group: Any = field(repr=False, default=None)
    representative: Any = field(repr=False, default=None)
    members: tuple = field(repr=False, default=())

    @property
    def source_material_ids(self):
        """The uploaded PDFs that contributed to this concept."""
        return sorted({item.material_id for item in self.members})


def _member_scan_key(learning_object, material_rank):
    """Where a chunk sits when the topic's PDFs are read one after another."""
    return (
        material_rank.get(learning_object.material_id, len(material_rank)),
        learning_object.order,
        learning_object.id,
    )


def _material_rank(node):
    """Upload order of the topic's materials, oldest first."""
    materials = node.materials.order_by("created_at", "id").values_list("id", flat=True)
    return {material_id: index for index, material_id in enumerate(materials)}


def _material_positions(records):
    """{material_id: {record index: position}}: each file's own sequence."""
    first_seen = defaultdict(dict)
    for index, record in enumerate(records):
        for item in record["members"]:
            key = (item.order, item.id)
            current = first_seen[item.material_id].get(index)
            if current is None or key < current:
                first_seen[item.material_id][index] = key

    positions = {}
    for material_id, keys in first_seen.items():
        ordered = sorted(keys, key=keys.get)
        positions[material_id] = {index: position for position, index in enumerate(ordered)}
    return positions


def _reference_ranking(positions, material_rank):
    """Which file settles a tie: most concepts, then earliest upload."""
    ranked = sorted(
        positions,
        key=lambda material_id: (
            -len(positions[material_id]),
            material_rank.get(material_id, len(material_rank)),
            material_id,
        ),
    )
    return {material_id: rank for rank, material_id in enumerate(ranked)}


def _order_records(records, material_rank):
    """Follow the reference file; a concept it lacks goes right after the one its own file puts before it."""
    positions = _material_positions(records)
    reference = _reference_ranking(positions, material_rank)
    order = []
    for material_id in sorted(positions, key=reference.get):
        sequence = sorted(positions[material_id], key=positions[material_id].get)
        for place, index in enumerate(sequence):
            if index in order:
                continue
            before = next((sequence[back] for back in range(place - 1, -1, -1) if sequence[back] in order), None)
            after = next((later for later in sequence[place + 1:] if later in order), None)
            if before is not None:
                order.insert(order.index(before) + 1, index)
            elif after is not None:
                order.insert(order.index(after), index)
            else:
                order.append(index)
    return [records[index] for index in order]


def concepts_for_topic(node):
    """The topic's concepts, in document order."""
    material_rank = _material_rank(node)

    records = []
    for group in node.learning_object_groups.prefetch_related(
        "learning_objects__material",
    ):
        members = ordered_members(group, ranked=material_rank)
        if not members:
            continue
        records.append({"group": group, "members": members})

    records = _order_records(records, material_rank)

    concepts = []
    for position, record in enumerate(records):
        group, members = record["group"], record["members"]
        representative = _representative_for(group, members)
        ordered = sorted(members, key=lambda item: _member_scan_key(item, material_rank))
        concepts.append(
            Concept(
                id=group.id,
                title=(group.label or representative.title or "").strip(),
                content=representative.content or "",
                section_title=representative.section_title or "",
                kind=representative.kind,
                order=position,
                group=group,
                representative=representative,
                members=tuple(ordered),
            )
        )
    return concepts


def _representative_for(group, members):
    """The member whose text is the concept's Standard version."""
    try:
        representative_id = assign_group_versions(group).get("representative_id")
    except Exception:
        representative_id = None

    if representative_id is not None:
        for item in members:
            if item.id == representative_id:
                return item

    for item in members:
        if item.represented_by_id is None:
            return item
    return members[0]
