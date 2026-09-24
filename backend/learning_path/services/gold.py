"""The teacher's gold-standard learning paths, and how a derivation measures up.

Gold concepts carry real lesson text exported from two uploaded lessons (see
``export_gold_concepts``). The report is what the manuscript's before/after
table quotes, and what ``test_gold_paths`` asserts on.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from . import criteria
from .publishing import order_with_links

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load_gold(topic_id):
    """``(map data, concepts)`` for a fixture, in the order it was written.

    Two fixture shapes load through here. In the older one (topics 62 and 79)
    every concept is one of the teacher's, and its ``key`` is unique. In the
    newer one (topic 152, written by ``export_live_concepts``) the concepts are
    the ones the pipeline actually derived, so **several concepts may share a
    key** -- the pipeline split something the teacher would have joined -- and a
    concept the teacher's map does not mention carries ``key: null``. An unkeyed
    concept still takes part in the derivation, because it changes document
    frequencies and the order; it is simply not scored against.
    """
    data = json.loads((FIXTURES / f"gold_topic_{topic_id}.json").read_text(encoding="utf-8"))
    concepts = []
    for index, row in enumerate(data["concepts"]):
        members = tuple(SimpleNamespace(**member) for member in row["members"])
        text = "\n".join(member.content for member in members)
        concepts.append(SimpleNamespace(
            id=index + 1,
            key=row["key"],
            title=row["title"],
            content=text,
            member_text=text,
            section_title=row["section_title"],
            kind=row.get("kind", "text"),
            order=index,
            members=members,
        ))
    return data, concepts


def _edges(decisions, key, verdict):
    """The scoreable edges of one verdict, as gold keys.

    Two kinds of derived edge cannot be scored against the teacher's map, and
    are dropped here rather than counted as something they are not:

    * an edge touching an **unkeyed** concept, which the map does not describe;
    * an edge **between two concepts carrying the same key**, which is the
      pipeline relating a concept to itself because it split it in two. That is
      a grouping result, not a prerequisite claim, and reporting it as a
      forbidden edge would blame the criteria for it.
    """
    return sorted({
        (key[row["prerequisite"].id], key[row["dependent"].id])
        for row in decisions
        if row["verdict"] == verdict
        and key[row["prerequisite"].id] is not None
        and key[row["dependent"].id] is not None
        and key[row["prerequisite"].id] != key[row["dependent"].id]
    })


def gold_report(data, concepts, decisions):
    key = {concept.id: concept.key for concept in concepts}
    by_key = {concept.key: concept for concept in concepts if concept.key}
    accepted = _edges(decisions, key, criteria.ACCEPTED)
    pending = _edges(decisions, key, criteria.PENDING)
    required = [tuple(edge) for edge in data["required"]]
    structural = set(data["structural"])
    parallel = [set(group) for group in data["parallel"]]
    known_missing = [tuple(edge) for edge in data.get("known_missing", [])]

    def forbidden(edge):
        before, after = edge
        return (
            before in structural
            or after in structural
            or any(before in group and after in group for group in parallel)
            or (after, before) in required
        )

    # Ordered from the decisions themselves, not from `accepted`: a fixture in
    # the live shape has edges `accepted` drops (unkeyed concepts, and the two
    # halves of a split concept), and those edges still move the path.
    links = [
        (row["prerequisite"].id, row["dependent"].id)
        for row in decisions if row["verdict"] == criteria.ACCEPTED
    ]
    ordered, _, ignored = order_with_links(concepts, links)
    # An unkeyed concept is taught somewhere in the order, but the teacher's map
    # says nothing about where; two concepts sharing a key are one concept
    # taught over two steps. Both collapse away before the order is compared.
    sequence = [key[concept.id] for concept in ordered if key[concept.id] is not None]
    order = [
        name for index, name in enumerate(sequence)
        if index == 0 or name != sequence[index - 1]
    ]
    accepted_set = set(accepted)
    missing_required = [edge for edge in required if edge not in accepted_set]
    return {
        "topic": data["topic_id"],
        "accepted": [list(edge) for edge in accepted],
        "pending": [list(edge) for edge in pending],
        "missing_required": [list(edge) for edge in missing_required],
        "unexpected_missing": [list(edge) for edge in missing_required if edge not in known_missing],
        "gaps_closed": [list(edge) for edge in known_missing if edge in accepted_set],
        "forbidden_accepted": [list(edge) for edge in accepted if forbidden(edge)],
        "extra_accepted": [list(edge) for edge in accepted if edge not in required and not forbidden(edge)],
        "order": order,
        "order_matches": order == data["expected_order"],
        "ignored_links": [[key[before], key[after]] for before, after in ignored],
        "unkeyed_concepts": sum(1 for concept in concepts if concept.key is None),
    }
