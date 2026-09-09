"""Derive prerequisite edges between the learning objects of one material.

The source document informs the graph but does not dictate the path. Two
decisions are kept deliberately apart:

* **Document order decides direction.** An edge may only run forward. A passage
  using a word the material does not define until later is using it loosely,
  not depending on it. Keeping this as a hard rule rather than as evidence is
  what makes the edge set a subgraph of a strict total order, and therefore
  acyclic -- though Kahn's still verifies rather than trusting it.
* **Evidence decides whether an edge exists at all.** Three criteria vote, each
  evaluated in both directions and each worth exactly one vote. No criterion
  outranks another, because there is no labelled data with which to justify a
  weighting.

The criteria are the ones the prerequisite-relation literature supports:
reference asymmetry (the RefD principle -- a reference only counts when it is
not returned), and distinctive term co-occurrence. See
docs/superpowers/specs/2026-09-10-edge-scoring-design.md for the reasoning and
the citations.
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

# A teacher's own edge is the most trustworthy statement in the graph -- it is
# the only one backed by a person who knows the subject. So is a chunk
# continuation, for a different reason: it is not a claim about prerequisites at
# all, but a fact about how our own chunker cut one passage in half.
CERTAIN_EDGE_WEIGHT = 1.0

# The three criteria that vote. Named here so evidence rows are self-describing.
CRITERION_BODY_REFERENCE = "body_reference"
CRITERION_SECTION_REFERENCE = "section_reference"
CRITERION_COOCCURRENCE = "cooccurrence"
# The relation this criterion reaches for -- a general concept containing the
# specific ones that follow -- is a recognised one; the multi-criteria
# literature detects it as category containment using an external knowledge
# base. MAVIA has none, so it is approximated locally: the material's opening
# definition is treated as containing the definitions that come after it. It is
# a proxy, and is described as one, which is why it is worth exactly one vote
# rather than a weight of its own.
CRITERION_DEFINITION_SCOPE = "definition_scope"
CRITERIA = (
    CRITERION_BODY_REFERENCE,
    CRITERION_SECTION_REFERENCE,
    CRITERION_COOCCURRENCE,
    CRITERION_DEFINITION_SCOPE,
)

# One net vote out of four. Raising this to 0.5 demands two and is the
# precision-first setting. Like every other cutoff in MAVIA this is a chosen
# operating value, not a calibrated one; the comparable published figure is
# 0.28 over ten criteria.
VOTE_THRESHOLD = 0.25


def learning_objects_for_material(material_id):
    """Teaching steps only.

    An object marked ``represented_by`` is taught through another one, so
    sequencing it would put content in the path that the lesson does not
    present.
    """
    return list(
        LearningObject.objects.filter(
            material_id=material_id,
            represented_by__isnull=True,
        ).order_by("order", "id")
    )


def _build_definer_index(learning_objects, positions):
    """Map each concept term to the object that introduces it.

    When several objects claim one term -- the chunker split the concept into
    "(Part 1 of 3)", or a teacher uploaded a second explanation of it -- the
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
        if len(claimants) > 1:
            ids = [lo.id for lo in claimants]
            for object_id in ids:
                co_definers[object_id].update(set(ids) - {object_id})
    return definer_by_term, co_definers


def _refers_to(text_normalized, other, own_concepts):
    """Does this passage name a concept the other one claims?"""
    for term in concept_terms(other.title):
        if term in own_concepts:
            continue
        if mentions(text_normalized, term):
            return term
    return None


def derive_edges(learning_objects):
    """Return candidate ``PrerequisiteEdge`` rows (unsaved) for these objects."""
    if len(learning_objects) < 2:
        return []

    positions = scan_positions(learning_objects)
    _, co_definers = _build_definer_index(learning_objects, positions)

    normalized_content = {lo.id: searchable_text(lo) for lo in learning_objects}
    normalized_section = {lo.id: normalize(lo.section_title) for lo in learning_objects}
    concepts = {lo.id: concept_terms(lo.title) for lo in learning_objects}
    terms_by_object = {
        lo.id: content_terms(f"{lo.title} {lo.content}") for lo in learning_objects
    }
    # Co-occurrence must rest on words peculiar to a few passages. Counting how
    # many passages use each word is what separates a real shared concept from
    # the lesson's background vocabulary.
    frequencies = document_frequencies(terms_by_object)
    distinctive_limit = distinctive_document_frequency_limit(len(learning_objects))

    document_order = sorted(learning_objects, key=lambda lo: positions[lo.id])

    # A lesson's opening definition is groundwork for the definitions that
    # follow it, even when none of them quotes it back: "A solid has a definite
    # shape" never says the word "matter", so no reference criterion can reach
    # it, and the concept the whole material rests on would be left floating.
    definitions = [lo for lo in document_order if definition_subject(lo.content)]
    definition_ids = {lo.id for lo in definitions}
    opening_definition_id = definitions[0].id if len(definitions) > 1 else None

    edges = []

    # Chunk continuation is settled before any voting and is exempt from it.
    # "(Part 1 of 3)" and "(Part 2 of 3)" are one passage the chunker had to
    # cut; letting thin evidence separate the halves would scatter a single
    # explanation across the path. It is also the one relation permitted
    # between co-definers, since the parts deliberately share a concept.
    continuation_pairs = set()
    for earlier, later in zip(document_order, document_order[1:]):
        earlier_part = part_marker(earlier.title)
        later_part = part_marker(later.title)
        if not earlier_part or not later_part:
            continue
        if earlier_part[0] != later_part[0] or earlier_part[2] != later_part[2]:
            continue
        if later_part[1] != earlier_part[1] + 1:
            continue
        continuation_pairs.add((earlier.id, later.id))
        edges.append(
            PrerequisiteEdge(
                prerequisite=earlier,
                dependent=later,
                signal=PrerequisiteEdge.Signal.CHUNK_CONTINUATION,
                weight=CERTAIN_EDGE_WEIGHT,
                evidence={
                    "base_title": earlier_part[0],
                    "part": later_part[1],
                    "of": later_part[2],
                    "note": "document order for a passage the chunker split",
                },
            )
        )

    for index, dependent in enumerate(document_order):
        for prerequisite in document_order[:index]:
            if (prerequisite.id, dependent.id) in continuation_pairs:
                continue
            if prerequisite.id in co_definers[dependent.id]:
                continue

            forward = {}
            backward = {}

            # C1 -- body reference asymmetry. The dependent naming the
            # prerequisite's concept only counts when the prerequisite does not
            # name the dependent's back. A mutual mention says the two are
            # related, not which one comes first.
            dependent_refs = _refers_to(
                normalized_content[dependent.id], prerequisite, concepts[dependent.id]
            )
            prerequisite_refs = _refers_to(
                normalized_content[prerequisite.id], dependent, concepts[prerequisite.id]
            )
            if dependent_refs and not prerequisite_refs:
                forward[CRITERION_BODY_REFERENCE] = dependent_refs
            elif prerequisite_refs and not dependent_refs:
                backward[CRITERION_BODY_REFERENCE] = prerequisite_refs

            # C2 -- the same test against section headings, which is weaker
            # evidence. Under equal-weight voting that is expressed by being a
            # separate criterion that can fail on its own, not by a smaller
            # number.
            dependent_section_refs = _refers_to(
                normalized_section[dependent.id], prerequisite, concepts[dependent.id]
            ) if normalized_section[dependent.id] else None
            prerequisite_section_refs = _refers_to(
                normalized_section[prerequisite.id], dependent, concepts[prerequisite.id]
            ) if normalized_section[prerequisite.id] else None
            if dependent_section_refs and not prerequisite_section_refs:
                forward[CRITERION_SECTION_REFERENCE] = dependent_section_refs
            elif prerequisite_section_refs and not dependent_section_refs:
                backward[CRITERION_SECTION_REFERENCE] = prerequisite_section_refs

            # C3 -- distinctive shared vocabulary. Symmetric by nature, so it
            # votes in the direction document order has already fixed and never
            # against it.
            shared = {
                term
                for term in terms_by_object[prerequisite.id] & terms_by_object[dependent.id]
                if frequencies[term] <= distinctive_limit
            }
            if len(shared) >= MIN_SHARED_TERMS_FOR_COOCCURRENCE:
                forward[CRITERION_COOCCURRENCE] = sorted(shared)[:10]

            # C4 -- definition scope. Only ever votes forward: the opening
            # definition is by construction the earliest, so this criterion
            # cannot argue against document order.
            if (
                opening_definition_id is not None
                and prerequisite.id == opening_definition_id
                and dependent.id in definition_ids
            ):
                forward[CRITERION_DEFINITION_SCOPE] = {
                    "defines": definition_subject(prerequisite.content),
                    "before_definition_of": definition_subject(dependent.content),
                }

            score = (len(forward) - len(backward)) / len(CRITERIA)
            if score < VOTE_THRESHOLD:
                continue

            edges.append(
                PrerequisiteEdge(
                    prerequisite=prerequisite,
                    dependent=dependent,
                    signal=PrerequisiteEdge.Signal.VOTED,
                    weight=round(score, 4),
                    evidence={
                        "score": round(score, 4),
                        "threshold": VOTE_THRESHOLD,
                        "voted_forward": forward,
                        "voted_backward": backward,
                        "document_frequency_limit": distinctive_limit,
                    },
                )
            )

    return edges
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
