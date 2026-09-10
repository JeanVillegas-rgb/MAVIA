"""Derive prerequisite edges between the learning objects of one material.

An edge asserts that understanding A is expected to support comprehension or
assessment of B -- broad enough that a learner failing B can sensibly be sent
back to A, narrow enough to exclude "these two passages share a topic".

The previous design scored *association*, which is dense: 28% of all possible
ordered pairs became edges, 88% of them on a single textual mention. Two
structural changes fix that:

* **Candidate generation and the prerequisite decision are separate.**
  Generating candidates may be generous; accepting them is strict. Density is
  then set by the strictest stage rather than the loosest.
* **Weak evidence cannot create an edge.** Mentions and co-occurrence generate
  and explain candidates; they never accept one.

Document order is no longer a hard directional filter. Real materials contain
forward references -- a property may be defined after the passage that uses it
-- so strong evidence may run backwards, and cycles are broken afterwards.
Ordering the lesson is not this module's job: see ``path_builder``.

See docs/superpowers/specs/2026-09-10-prerequisite-redesign-design.md.
"""

from django.db import transaction

from lessons.models import LearningObject

from ..models import PrerequisiteEdge
from .evidence import accept, build_context, gather, suppressed

# Structural, not inferred: "(Part 1 of 3)" and "(Part 2 of 3)" are one passage
# the chunker cut, and a teacher's own edge is the only one backed by a person
# who knows the subject.
CERTAIN_EDGE_WEIGHT = 1.0


def learning_objects_for_material(material_id):
    """Teaching steps only.

    An object marked ``represented_by`` is taught through another one, so
    sequencing it would put content in the path the lesson does not present.
    """
    return list(
        LearningObject.objects.filter(
            material_id=material_id,
            represented_by__isnull=True,
        ).order_by("order", "id")
    )


def _continuation_edges(ctx):
    """Split parts stay adjacent and in order, regardless of evidence."""
    from .text_signals import part_marker

    ordered = sorted(ctx.objects, key=lambda item: ctx.positions[item.id])
    edges, pairs = [], set()
    for earlier, later in zip(ordered, ordered[1:]):
        first, second = part_marker(earlier.title), part_marker(later.title)
        if not first or not second:
            continue
        if first[0] != second[0] or first[2] != second[2]:
            continue
        if second[1] != first[1] + 1:
            continue
        pairs.add((earlier.id, later.id))
        edges.append(PrerequisiteEdge(
            prerequisite=earlier,
            dependent=later,
            signal=PrerequisiteEdge.Signal.CHUNK_CONTINUATION,
            weight=CERTAIN_EDGE_WEIGHT,
            evidence={
                "base_title": first[0],
                "part": second[1],
                "of": second[2],
                "note": "document order for a passage the chunker split",
            },
        ))
    return edges, pairs


def _is_candidate(a, b, strong, medium, weak):
    """Generous on purpose: this only decides what gets *examined*."""
    return bool(strong or medium or weak)


def derive_edges(learning_objects, material=None):
    """Return candidate ``PrerequisiteEdge`` rows (unsaved) for these objects."""
    if len(learning_objects) < 2:
        return []

    if material is None:
        material = learning_objects[0].material

    ctx = build_context(material, learning_objects)
    edges, continuation_pairs = _continuation_edges(ctx)

    for a in ctx.objects:
        for b in ctx.objects:
            if a.id == b.id or (a.id, b.id) in continuation_pairs:
                continue

            strong, medium, weak = gather(a, b, ctx)
            if not _is_candidate(a, b, strong, medium, weak):
                continue

            # Strong evidence is gathered BEFORE suppression, because an
            # explicit statement from the author overrides the suppressors.
            # Rejecting first would make that override unreachable.
            author_says_so = any(item.type == "explicit_dependency" for item in strong)
            if suppressed(a, b, ctx, author_says_so=author_says_so, strong=strong):
                continue

            if not accept(strong, medium):
                continue

            edges.append(PrerequisiteEdge(
                prerequisite=a,
                dependent=b,
                signal=PrerequisiteEdge.Signal.VOTED,
                weight=CERTAIN_EDGE_WEIGHT if strong else 0.5,
                evidence={
                    "tier": "strong" if strong else "medium",
                    "strong": [item.type for item in strong],
                    "medium": [item.type for item in medium],
                    "weak": [item.type for item in weak],
                    "detail": {item.type: item.detail for item in strong + medium},
                },
            ))

    return _break_cycles(edges, ctx)


def _break_cycles(edges, ctx):
    """Strong evidence may run against document order, so a cycle is possible.

    Drop the weakest edge in a cycle; on a tie, drop the one running backwards
    through the document. Dropped edges are returned to nobody -- they simply do
    not exist -- but the rule is deterministic.
    """
    from .topological_sort import GraphCycleError, kahn_topological_order

    kept = list(edges)
    for _ in range(len(kept)):
        pairs = [(e.prerequisite_id, e.dependent_id) for e in kept]
        try:
            kahn_topological_order([item.id for item in ctx.objects], pairs)
            return kept
        except GraphCycleError as cycle:
            unresolved = set(cycle.unresolved_nodes)
            in_cycle = [
                e for e in kept
                if e.prerequisite_id in unresolved and e.dependent_id in unresolved
            ]
            if not in_cycle:
                return kept
            victim = min(in_cycle, key=lambda e: (
                e.weight,
                ctx.positions[e.prerequisite_id] < ctx.positions[e.dependent_id],
            ))
            kept.remove(victim)
    return kept


@transaction.atomic
def rebuild_edges_for_material(material_id):
    """Replace this material's DERIVED edges with a freshly derived set.

    Teacher-added edges are left alone. A teacher correcting the graph should
    not have that correction silently undone the next time the material is
    reprocessed, so re-derivation only owns the rows it created. A derived edge
    that duplicates a teacher edge is skipped, so the teacher's row (and its
    reason) is the one that survives.
    """
    learning_objects = learning_objects_for_material(material_id)
    PrerequisiteEdge.objects.filter(
        dependent__material_id=material_id,
        source=PrerequisiteEdge.Source.DERIVED,
    ).delete()

    teacher_pairs = set(
        PrerequisiteEdge.objects.filter(
            dependent__material_id=material_id,
            source=PrerequisiteEdge.Source.TEACHER,
        ).values_list("prerequisite_id", "dependent_id")
    )

    edges = [
        edge
        for edge in derive_edges(learning_objects)
        if (edge.prerequisite_id, edge.dependent_id) not in teacher_pairs
    ]
    PrerequisiteEdge.objects.bulk_create(edges)
    return edges
