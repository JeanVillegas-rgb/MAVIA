"""Derive prerequisite edges between the learning objects of one material.

The source document informs the graph but does not dictate the path: text
signals decide *which* objects depend on which, and the document's own sequence
is used only to orient an edge (a passage cannot depend on one that has not been
presented yet) and, much later, as the lowest-priority tie-breaker in the sort.
"""

from django.db import transaction

from lessons.models import LearningObject

from ..models import PrerequisiteEdge
from .text_signals import (
    MIN_SHARED_TERMS_FOR_COOCCURRENCE,
    concept_terms,
    content_terms,
    definition_subject,
    distinctive_document_frequency_limit,
    document_frequencies,
    mentions,
    normalize,
    part_marker,
    scan_positions,
    searchable_text,
)

# A teacher's own edge is the most trustworthy statement in the graph — it is
# the only one backed by a person who knows the subject.
TEACHER_EDGE_WEIGHT = 1.0

SIGNAL_WEIGHTS = {
    PrerequisiteEdge.Signal.TITLE_REFERENCE: 1.0,
    PrerequisiteEdge.Signal.SECTION_REFERENCE: 0.6,
    PrerequisiteEdge.Signal.TERM_COOCCURRENCE: 0.4,
    PrerequisiteEdge.Signal.CHUNK_CONTINUATION: 1.0,
    PrerequisiteEdge.Signal.DEFINITION_SCOPE: 0.8,
    PrerequisiteEdge.Signal.TEACHER_AUTHORED: TEACHER_EDGE_WEIGHT,
}


def learning_objects_for_material(material_id):
    return list(
        LearningObject.objects.filter(material_id=material_id).order_by("order", "id")
    )


def _build_definer_index(learning_objects, positions):
    """Map each concept term to the object that introduces it.

    When several objects claim one term — the chunker split the concept into
    "(Part 1 of 3)", or a teacher uploaded a second explanation of it — the
    earliest is the definer, and all the claimants are recorded as each other's
    co-definers so no edge is ever drawn between two takes on the same concept.
    """
    claims = {}
    for learning_object in learning_objects:
        for term in concept_terms(learning_object.title):
            claims.setdefault(term, []).append(learning_object)

    definer_by_term = {}
    co_definers = {lo.id: set() for lo in learning_objects}
    for term, claimants in claims.items():
        claimants.sort(key=lambda lo: positions[lo.id])
        definer_by_term[term] = claimants[0]
        claimant_ids = {lo.id for lo in claimants}
        for learning_object in claimants:
            co_definers[learning_object.id] |= claimant_ids - {learning_object.id}
    return definer_by_term, co_definers


def derive_edges(learning_objects):
    """Return candidate ``PrerequisiteEdge`` rows (unsaved) for these objects."""
    if len(learning_objects) < 2:
        return []

    positions = scan_positions(learning_objects)
    definer_by_term, co_definers = _build_definer_index(learning_objects, positions)

    normalized_content = {lo.id: searchable_text(lo) for lo in learning_objects}
    normalized_section = {lo.id: normalize(lo.section_title) for lo in learning_objects}
    terms_by_object = {
        lo.id: content_terms(f"{lo.title} {lo.content}") for lo in learning_objects
    }
    # Co-occurrence must rest on words peculiar to a few passages. Counting how
    # many passages use each word is what separates a real shared concept from
    # the lesson's background vocabulary.
    frequencies = document_frequencies(terms_by_object)
    distinctive_limit = distinctive_document_frequency_limit(len(learning_objects))

    edges = {}

    def add(prerequisite, dependent, signal, evidence, allow_co_definers=False):
        if prerequisite.id == dependent.id:
            return
        if not allow_co_definers and prerequisite.id in co_definers[dependent.id]:
            return
        # An edge may only run forward through the document. A passage that uses
        # a word the material does not define until later is using it loosely,
        # not depending on it; recording that as a dependency would invert the
        # lesson (the opening definition would end up taught last). This also
        # makes the edge set a subgraph of a strict total order, hence acyclic —
        # but Kahn's still verifies that rather than trusting it, so a future
        # hand-authored edge cannot silently break the sort.
        if positions[prerequisite.id] >= positions[dependent.id]:
            return
        key = (prerequisite.id, dependent.id, signal)
        if key in edges:
            return
        edges[key] = PrerequisiteEdge(
            prerequisite=prerequisite,
            dependent=dependent,
            signal=signal,
            weight=SIGNAL_WEIGHTS[signal],
            evidence=evidence,
        )

    document_order = sorted(learning_objects, key=lambda lo: positions[lo.id])

    # Signal 0: chunk continuation. "(Part 1 of 3)" and "(Part 2 of 3)" are one
    # passage the chunker had to cut, so they must stay adjacent and in order.
    # This is the one relation allowed between co-definers, because the parts
    # deliberately share a concept -- suppressing it would let depth layering
    # scatter halves of a single explanation across the path.
    for earlier, later in zip(document_order, document_order[1:]):
        earlier_part = part_marker(earlier.title)
        later_part = part_marker(later.title)
        if not earlier_part or not later_part:
            continue
        if earlier_part[0] != later_part[0] or earlier_part[2] != later_part[2]:
            continue
        if later_part[1] != earlier_part[1] + 1:
            continue
        add(
            earlier,
            later,
            PrerequisiteEdge.Signal.CHUNK_CONTINUATION,
            {"base_title": earlier_part[0], "part": later_part[1], "of": later_part[2]},
            allow_co_definers=True,
        )

    # Signal 1 and 2: reference direction. A passage that names a concept depends
    # on whichever passage introduced that concept. A mention in the body is the
    # strong form; a mention only in the section heading is the weak form.
    for dependent in learning_objects:
        dependent_concepts = concept_terms(dependent.title)
        for term, definer in definer_by_term.items():
            if definer.id == dependent.id or term in dependent_concepts:
                continue
            if mentions(normalized_content[dependent.id], term):
                add(
                    definer,
                    dependent,
                    PrerequisiteEdge.Signal.TITLE_REFERENCE,
                    {"term": term, "matched_in": "content"},
                )
            elif normalized_section[dependent.id] and mentions(
                normalized_section[dependent.id], term
            ):
                add(
                    definer,
                    dependent,
                    PrerequisiteEdge.Signal.SECTION_REFERENCE,
                    {"term": term, "matched_in": "section_title"},
                )

    # Signal 3: definition scope. A lesson's opening definition is groundwork for
    # the definitions that follow it, even when none of them quotes it back --
    # "A solid has a definite shape" never says the word "matter", so no
    # reference signal can connect them, and the concept the whole material rests
    # on would otherwise be left floating as an unconnected starting point.
    definitions = [
        learning_object
        for learning_object in document_order
        if definition_subject(learning_object.content)
    ]
    if len(definitions) > 1:
        opening_definition = definitions[0]
        for later_definition in definitions[1:]:
            add(
                opening_definition,
                later_definition,
                PrerequisiteEdge.Signal.DEFINITION_SCOPE,
                {
                    "defines": definition_subject(opening_definition.content),
                    "before_definition_of": definition_subject(later_definition.content),
                },
            )

    # Signal 4: co-occurrence, used only to rescue objects that no other signal
    # reached. Without it an object that paraphrases earlier material without
    # naming it would look like a root and float to the front of the path.
    reached = {dependent_id for _, dependent_id, _ in edges}
    ordered = document_order
    for index, dependent in enumerate(ordered):
        if index == 0 or dependent.id in reached:
            continue
        best = None
        for candidate_index, candidate in enumerate(ordered[:index]):
            if candidate.id in co_definers[dependent.id]:
                continue
            shared = {
                term
                for term in terms_by_object[candidate.id] & terms_by_object[dependent.id]
                if frequencies[term] <= distinctive_limit
            }
            if len(shared) < MIN_SHARED_TERMS_FOR_COOCCURRENCE:
                continue
            # Most shared terms wins; ties go to the nearest preceding object,
            # which is the more likely immediate prerequisite.
            score = (len(shared), candidate_index)
            if best is None or score > best[0]:
                best = (score, candidate, shared)
        if best is not None:
            _, candidate, shared = best
            add(
                candidate,
                dependent,
                PrerequisiteEdge.Signal.TERM_COOCCURRENCE,
                {
                    "distinctive_shared_terms": sorted(shared)[:10],
                    "shared_count": len(shared),
                    "document_frequency_limit": distinctive_limit,
                },
            )

    return list(edges.values())


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
