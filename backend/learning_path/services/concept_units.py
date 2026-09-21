"""One teaching unit of a topic: a concept, not a chunk.

A topic holds several uploaded PDFs teaching overlapping content, and grouping
has already decided which chunks across those files teach the same thing. The
concept is therefore what a path should order -- a learner meets "Solid" once,
with its three versions, rather than three times from three PDFs.

A ``Concept`` exposes the surface of a learning object -- ``id``, ``title``,
``content``, ``section_title``, ``kind`` and ``order`` -- so the text helpers and
criteria that read those fields work on concepts unchanged.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from course.version_assignment import assign_group_versions
from lessons.services.concept_bundles import bundle_heading, ordered_members

from .text_signals import part_marker, strip_part_suffix


@dataclass(frozen=True)
class Concept:
    """A ``LearningObjectGroup`` wearing the interface of a learning object."""

    id: int
    title: str
    content: str
    section_title: str
    kind: str
    order: int
    # Every member's text, one PDF after another. The criteria read this so a
    # concept speaks with all its PDFs' wording, not only the Normal version's.
    member_text: str = ""
    group: Any = field(repr=False, default=None)
    representative: Any = field(repr=False, default=None)
    members: tuple = field(repr=False, default=())

    @property
    def source_material_ids(self):
        """Which uploaded PDFs contributed to this concept.

        A single path replaces the per-PDF paths, so this is how a step is
        traced back to the files it came from.
        """
        return sorted({item.material_id for item in self.members})


def _member_scan_key(learning_object, material_rank):
    """Where a chunk sits when the topic's PDFs are read one after another."""
    return (
        material_rank.get(learning_object.material_id, len(material_rank)),
        learning_object.order,
        learning_object.id,
    )


def _material_rank(node):
    """Upload order of the topic's materials, oldest first.

    The same ordering the topic endpoint already used when it returned one path
    per file, so "earlier in the topic" keeps meaning what it meant.
    """
    materials = node.materials.order_by("created_at", "id").values_list("id", flat=True)
    return {material_id: index for index, material_id in enumerate(materials)}


def _passage_runs(records):
    """Yield ``(total, run)`` for each split passage found among these records.

    The chunker cuts an oversized passage into "(Part 1 of 3)" pieces, and those
    pieces are one passage. Finding them is not just a matter of matching titles:
    one file carries *three* passages all titled "Diagram description (Part 1 of
    2)" / "(Part 2 of 2)", one under each state of matter. Matching on the title
    alone fuses all three into a single nonsense concept.

    What separates them is position. Parts of one passage sit next to each other
    in the document, so the parts of a title are walked in document order and a
    new run starts each time the numbering returns to 1.
    """
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
    """Collapse the groups holding one split passage into a single concept.

    Grouping puts each chunk with the versions of *that chunk*, so a passage the
    chunker cut lands in several groups: "SOLID (Part 1 of 3)" sits with the
    other files' "Solid", while parts 2 and 3 sit alone. Left that way one
    concept appears in the path three times -- once properly and twice as a
    fragment.

    Merges compose. A group can belong to two passages at once: one holds
    "LIQUID (Part 2 of 3)" *and* another file's liquid diagram, because grouping
    judged those to teach the same thing. Treating the runs as separate claims
    and letting whichever is seen first win would absorb that group into Liquid
    and orphan the diagram's second half. Taking every run as a union instead
    lets the whole diagram follow the group it belongs with.

    Merging stays conservative about what counts as a passage: every declared
    part must be present *and* the parts must be contiguous in the document.
    A run with a gap is not a whole passage, and guessing at the gap would join
    text that may not belong together.
    """
    parent = {id(record): id(record) for record in records}

    def find(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for total, run in _passage_runs(records):
        if [number for _, number, _ in run] != list(range(1, total + 1)):
            continue
        orders = [order for order, _, _ in run]
        if orders != list(range(orders[0], orders[0] + len(orders))):
            continue
        ids = [id(record) for _, _, record in run]
        for other in ids[1:]:
            union(ids[0], other)

    components = {}
    for record in records:
        components.setdefault(find(id(record)), []).append(record)

    merged = []
    for component in components.values():
        # The earliest group speaks for the concept -- it holds the passage's
        # opening, and with it the other files' versions of the same concept.
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


def _material_positions(records):
    """``{material_id: {record index: position}}`` -- each file's own sequence.

    A concept's position in a file is where its first member there appears, and
    positions are renumbered 0, 1, 2 ... over the concepts that file teaches, so
    they compare sequences rather than raw chunk numbers.
    """
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
    """Which file's order settles a disagreement: ``{material_id: rank}``.

    The file teaching the most concepts leads -- the fuller lesson is the
    reference -- and between files of the same size the one uploaded first.
    """
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
    """Merge every file's sequence into one, without inventing an order.

    Three rules, in priority order:

    1. **Two concepts that appear in the same files keep the order most of
       those files give them.** With three files, two agreeing outvote one.
    2. **When the files are split evenly, the reference file decides** -- the
       one teaching the most concepts, then the one uploaded first. With two
       files every disagreement is an even split, so this is the common case.
    3. **A concept only some files teach keeps its neighbours from those
       files.** Rules 1 and 2 already say "after X, before Y" for it; nothing
       else pulls it towards the end just because another file lacks it.

    Blending positions was tried and rejected: averaging, or taking the later
    of two positions, put "Comparing" between Liquid and Gas when one file
    taught it first and the other last -- an order neither author wrote.
    Settling every pair by a real file's order avoids that. Concepts no file
    orders relative to each other are interleaved by relative position, which
    cannot contradict any file because none of them says anything about it.
    """
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
        # Only reachable with three or more files whose majorities form a loop
        # (A before B, B before C, C before A). Some rule has to give way; the
        # concept earliest by relative position goes first, deterministically.
        chosen = min(ready or remaining, key=keys.get)
        order.append(records[chosen])
        remaining.discard(chosen)
        for following in successors[chosen]:
            indegree[following] -= 1
    return order


def concepts_for_topic(node):
    """Return this topic's concepts, in the order the topic presents them.

    Each uploaded file has its own sequence, and the files do not always agree
    -- one opens with a comparison figure that another shows after all three
    states. ``_order_records`` merges them; see it for the rules. ``order`` on
    each concept is its position in that merged sequence.
    """
    material_rank = _material_rank(node)

    records = []
    for group in node.learning_object_groups.prefetch_related(
        "learning_objects__material",
    ):
        # Every member belongs to the concept, including those marked
        # `represented_by`. At learning-object level that flag means "taught
        # through another object, so do not sequence it" -- correct there, and
        # exactly backwards here: the concept *is* the step, and those members
        # are the other PDFs' versions of it. Filtering them out made every
        # concept report a single source and hid the cross-PDF grouping
        # entirely -- 11 multi-PDF concepts came back as none.
        # `material_rank` is the same upload ranking `ordered_members`
        # would otherwise derive per concept, at two queries each.
        members = ordered_members(group, ranked=material_rank)
        if not members:
            continue
        records.append({
            "group": group,
            "members": members,
            "earliest": min(_member_scan_key(item, material_rank) for item in members),
        })

    records = _merge_split_passages(records)
    records = _order_records(records, material_rank)

    concepts = []
    for position, record in enumerate(records):
        group, members = record["group"], record["members"]
        representative = _representative_for(group, members)
        # `members` is in bundle order per its own source group, but
        # `_merge_split_passages` concatenates *several* groups' member lists
        # in whatever order the groups' queryset happened to return them --
        # not document order. Re-sorting the whole set by scan position
        # (material upload order, then document order) gives the concept one
        # coherent order regardless of merging. For a concept from a single,
        # unmerged group this reproduces the same order `ordered_members`
        # already gave it: both rank by (material rank, object order, id).
        ordered = sorted(members, key=lambda item: _member_scan_key(item, material_rank))
        member_text = "\n".join(item.content or "" for item in ordered)
        # The representative's own bundle: the objects its *own* concept holds
        # from its material, in document order. Built from members already in
        # hand rather than a fresh `bundles_for_group(group)` query, which
        # would miss a representative merged in from a different source group
        # -- hence the match on `group_id` rather than on `group`.
        #
        # Restricted to that one group on purpose: `_merge_split_passages`
        # concatenates several groups' members here, so counting every member
        # of the material would let "SOLID (Part 1 of 2)", a bundle of one,
        # reach two and take its section heading. Real material files SOLID
        # and LIQUID under a single "Matter" section, so both concepts would
        # be named "Matter" and the criteria's same-name veto would delete
        # every edge between them -- the very failure §3.5 was amended to
        # close.
        representative_bundle = [
            item for item in ordered
            if item.material_id == representative.material_id
            and item.group_id == representative.group_id
        ]
        concepts.append(
            Concept(
                id=group.id,
                # The representative's bundle's own heading -- but only when
                # the bundle actually holds several objects. A multi-object
                # bundle often opens with a figure that has no heading of its
                # own, and naming the concept after that figure left it with
                # no usable name for the criteria; the heading fixes that
                # ("figure + Matter" -> "Matter", "Shape + Volume + ..." ->
                # "Comparing the Three States"). A *single*-object bundle has
                # no such problem, and the section it sits under names the
                # whole section, not the object -- real material has three
                # objects, "Solid", "Liquid" and "Gas", each alone under a
                # "Matter" heading; titling all three "Matter" would give them
                # one shared name and the criteria's same-name veto would then
                # delete every edge between them. Falls back to the
                # representative's title, then the group's label.
                title=(
                    (bundle_heading(representative_bundle) if len(representative_bundle) >= 2 else "")
                    # A bundle of one falls back to the representative's own
                    # title -- but a merged split passage's representative is
                    # still titled with the chunker's "(Part 1 of 2)" marker,
                    # and that marker is what the criteria would match
                    # against if it reached the concept list unstripped.
                    or strip_part_suffix(representative.title)
                    or group.label
                    or ""
                ).strip(),
                # "Normal" is the representative's own text -- unlike Simplified
                # and Elaborated it is not a stored slot.
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
    """The member whose text speaks for the concept.

    Resolved through ``assign_group_versions`` -- the same call the learning
    resources payload makes -- so the path shows the very Normal version the
    teacher reviewed, rather than a second opinion about which member is
    canonical.
    """
    try:
        representative_id = assign_group_versions(group).get("representative_id")
    except Exception:  # noqa: BLE001 -- a path must still build if this fails
        representative_id = None

    if representative_id is not None:
        for item in members:
            if item.id == representative_id:
                return item

    # Failing that, prefer a member that is not itself taught through another
    # one -- a represented member is a version of the concept, not its voice.
    for item in members:
        if item.represented_by_id is None:
            return item
    return members[0]
