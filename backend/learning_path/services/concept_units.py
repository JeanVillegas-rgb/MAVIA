"""A topic's grouped learning objects as ordered concepts."""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from course.version_assignment import assign_group_versions
from lessons.services.concept_bundles import bundle_heading, ordered_members

from .concept_text import strip_numbering
from .text_signals import part_marker, strip_part_suffix


@dataclass(frozen=True)
class Concept:
    """A LearningObjectGroup with a learning object's fields."""

    id: int
    title: str
    content: str
    section_title: str
    kind: str
    order: int
    member_text: str = ""
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


def _passage_runs(records):
    """Yield (total, run) for each split "(Part n of m)" passage, in document order."""
    by_key = {}
    for record in records:
        for item in record["members"]:
            marker = part_marker(item.title)
            if not marker:
                continue
            base, number, total = marker
            key = (item.material_id, base.casefold(), total)
            by_key.setdefault(key, []).append((item.order, number, record))

    runs = []
    for (_, _, total), entries in by_key.items():
        entries.sort(key=lambda entry: entry[0])
        current = []
        for entry in entries:
            if entry[1] == 1 and current:
                runs.append((total, current))
                current = []
            current.append(entry)
        if current:
            runs.append((total, current))
    return runs


def _merge_split_passages(records):
    """Merge the groups holding one split passage into a single concept."""
    joins = []
    for total, run in _passage_runs(records):
        if [number for _, number, _ in run] != list(range(1, total + 1)):
            continue
        orders = [order for order, _, _ in run]
        if orders != list(range(orders[0], orders[0] + len(orders))):
            continue
        joins.extend((run[0][2], record) for _, _, record in run[1:])
    return _join_records(records, joins)


def _join_records(records, joins):
    """One record per connected set of joins."""
    parent = {id(record): id(record) for record in records}

    def find(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for left, right in joins:
        left_root, right_root = find(id(left)), find(id(right))
        if left_root != right_root:
            parent[right_root] = left_root

    components = {}
    for record in records:
        components.setdefault(find(id(record)), []).append(record)

    merged = []
    for component in components.values():
        primary = min(component, key=lambda record: record["earliest"])
        members = []
        for record in component:
            members.extend(record["members"])
        merged.append({
            "group": primary["group"],
            "members": members,
            "earliest": primary["earliest"],
        })
    return merged


def _name_key(name):
    """A heading without its list number or "(Part n of m)"."""
    return " ".join(strip_numbering(strip_part_suffix(name or "")).casefold().split())


def _headings(members):
    return {
        " ".join((getattr(item, "section_title", "") or "").casefold().split())
        for item in members
        if (getattr(item, "section_title", "") or "").strip()
    }


def same_concept(first, second):
    """True when two units are one concept that grouping kept apart."""
    (first_name, first_members), (second_name, second_members) = first, second
    key = _name_key(first_name)
    if not key or key != _name_key(second_name):
        return False
    shared = {item.material_id for item in first_members} & {item.material_id for item in second_members}
    if shared:
        return any(
            abs(left.order - right.order) == 1
            for left in first_members for right in second_members
            if left.material_id == right.material_id
        )
    left, right = _headings(first_members), _headings(second_members)
    return not (left and right and not (left & right))


def _join_same_name(records):
    """Join the records of one concept that grouping split under one name."""
    units = [(record["group"].label, record["members"]) for record in records]
    joins = [
        (records[first], records[second])
        for first in range(len(records))
        for second in range(first + 1, len(records))
        if same_concept(units[first], units[second])
    ]
    return _join_records(records, joins) if joins else records


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
    """Merge every file's concept order into one: majority, then the reference file."""
    positions = _material_positions(records)
    reference = _reference_ranking(positions, material_rank)
    count = len(records)

    successors = defaultdict(set)
    indegree = [0] * count
    for first in range(count):
        for second in range(first + 1, count):
            shared = [m for m in positions if first in positions[m] and second in positions[m]]
            if not shared:
                continue
            first_leads = sum(1 for m in shared if positions[m][first] < positions[m][second])
            second_leads = len(shared) - first_leads
            if first_leads == second_leads:
                lead = min(shared, key=reference.get)
                first_wins = positions[lead][first] < positions[lead][second]
            else:
                first_wins = first_leads > second_leads
            before, after = (first, second) if first_wins else (second, first)
            successors[before].add(after)
            indegree[after] += 1

    def tie_key(index):
        spots = [
            positions[m][index] / max(1, len(positions[m]) - 1)
            for m in positions if index in positions[m]
        ]
        return (
            sum(spots) / len(spots),
            min(reference[m] for m in positions if index in positions[m]),
            records[index]["earliest"],
        )

    keys = {index: tie_key(index) for index in range(count)}
    order, remaining = [], set(range(count))
    while remaining:
        ready = [index for index in remaining if indegree[index] == 0]
        chosen = min(ready or remaining, key=keys.get)
        order.append(records[chosen])
        remaining.discard(chosen)
        for following in successors[chosen]:
            indegree[following] -= 1
    return order


def concepts_for_topic(node):
    """The topic's concepts, in merged document order."""
    material_rank = _material_rank(node)

    records = []
    for group in node.learning_object_groups.prefetch_related(
        "learning_objects__material",
    ):
        members = ordered_members(group, ranked=material_rank)
        if not members:
            continue
        records.append({
            "group": group,
            "members": members,
            "earliest": min(_member_scan_key(item, material_rank) for item in members),
        })

    records = _merge_split_passages(records)
    records = _join_same_name(records)
    records = _order_records(records, material_rank)

    concepts = []
    for position, record in enumerate(records):
        group, members = record["group"], record["members"]
        representative = _representative_for(group, members)
        ordered = sorted(members, key=lambda item: _member_scan_key(item, material_rank))
        member_text = "\n".join(item.content or "" for item in ordered)
        representative_bundle = [
            item for item in ordered
            if item.material_id == representative.material_id
            and item.group_id == representative.group_id
        ]
        concepts.append(
            Concept(
                id=group.id,
                title=(
                    (bundle_heading(representative_bundle) if len(representative_bundle) >= 2 else "")
                    or strip_part_suffix(representative.title)
                    or group.label
                    or ""
                ).strip(),
                content=representative.content or "",
                section_title=representative.section_title or "",
                kind=representative.kind,
                order=position,
                member_text=member_text,
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
