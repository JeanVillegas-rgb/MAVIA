"""The teacher's gold-standard learning paths, and how a derivation measures up.

Gold concepts carry real lesson text exported from two uploaded lessons (see
``export_gold_concepts``). The report is what the manuscript's before/after
table quotes, and what ``test_gold_paths`` asserts on.
"""

import json
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

from . import criteria, embeddings
from .clues import find_term_owners, pair_votes
from .concept_text import material_positions, prepare
from .direction_votes import VOTES, build_block_matrix, cast_votes
from .fusion import CLUES
from .publishing import order_with_links
from .relatedness import relatedness

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load_gold(topic_id):
    """``(map data, concepts)`` for a fixture, in the order it was written.

    The file is ``gold_topic_<topic_id>.json``; ``topic_id`` may be a string
    such as ``"348alt"`` for a second key on one topic.

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


def kendall_tau(order, expected):
    """Agreement between two orders over the keys both contain: 1 same, -1 reversed.

    Replaces the all-or-nothing order match, which fails on a single swap and
    says nothing about how close a path is. ``None`` below two shared keys.
    """
    rank = {}
    for index, key in enumerate(order):
        rank.setdefault(key, index)
    shared = [key for key in expected if key in rank]
    if len(shared) < 2:
        return None
    concordant = discordant = 0
    for index, earlier in enumerate(shared):
        for later in shared[index + 1:]:
            if rank[earlier] < rank[later]:
                concordant += 1
            else:
                discordant += 1
    return (concordant - discordant) / (concordant + discordant)


def covered_links(data, concepts, decisions):
    """The key's required links a learner can be sent back through.

    A required link is covered when accepted links lead from a concept carrying
    its first key to one carrying its second, directly or through a chain --
    the adaptive engine walks chains, so a direct link is not needed. Chains may
    pass through unkeyed concepts and either half of a split concept.
    """
    successors = defaultdict(set)
    for row in decisions:
        if row["verdict"] == criteria.ACCEPTED:
            successors[row["prerequisite"].id].add(row["dependent"].id)
    key = {concept.id: concept.key for concept in concepts}

    def reached_from(start):
        seen, waiting = set(), [start]
        while waiting:
            for following in successors[waiting.pop()]:
                if following not in seen:
                    seen.add(following)
                    waiting.append(following)
        return seen

    reach = {concept.id: reached_from(concept.id) for concept in concepts}
    return [
        [before, after] for before, after in data["required"]
        if any(key[reached] == after
               for concept in concepts if concept.key == before
               for reached in reach[concept.id])
    ]


def order_only_decisions(concepts):
    """Baseline: each concept after the one just before it in the topic's order; no text read."""
    return [
        {"prerequisite": before, "dependent": after, "verdict": criteria.ACCEPTED,
         "evidence": {"rule": "order-only", "confidence": 1.0}, "cross_section": False}
        for before, after in zip(concepts, concepts[1:])
    ]


def gold_report(data, concepts, decisions, build_on_latest=False):
    key = {concept.id: concept.key for concept in concepts}
    by_key = {concept.key: concept for concept in concepts if concept.key}
    accepted = _edges(decisions, key, criteria.ACCEPTED)
    pending = _edges(decisions, key, criteria.PENDING)
    required = [tuple(edge) for edge in data["required"]]
    structural = set(data["structural"])
    parallel = [set(group) for group in data["parallel"]]
    known_missing = [tuple(edge) for edge in data.get("known_missing", [])]

    explicit_forbidden = {tuple(edge) for edge in data.get("forbidden", [])}

    def forbidden(edge):
        before, after = edge
        return (
            edge in explicit_forbidden
            or before in structural
            or after in structural
            or any(before in group and after in group for group in parallel)
            or (after, before) in required
        )

    # Gold maps list direct edges only. An accepted edge the required chain
    # implies ("matter -> comparing" through solid) is correct, not an extra.
    implied = set(required)
    changed = True
    while changed:
        changed = False
        for (first, middle) in list(implied):
            for (other, last) in list(implied):
                if middle == other and first != last and (first, last) not in implied:
                    implied.add((first, last))
                    changed = True

    # Ordered from the decisions themselves, not from `accepted`: a fixture in
    # the live shape has edges `accepted` drops (unkeyed concepts, and the two
    # halves of a split concept), and those edges still move the path.
    accepted_rows = [row for row in decisions if row["verdict"] == criteria.ACCEPTED]
    links = [(row["prerequisite"].id, row["dependent"].id) for row in accepted_rows]
    confidence = {
        (row["prerequisite"].id, row["dependent"].id): (row.get("evidence") or {}).get("confidence", 0.0)
        for row in accepted_rows
    }
    ordered, _, ignored = order_with_links(concepts, links, confidence, build_on_latest=build_on_latest)
    # An unkeyed concept is taught somewhere in the order, but the teacher's map
    # says nothing about where; two concepts sharing a key are one concept
    # taught over two steps. Both collapse away before the order is compared.
    sequence = [key[concept.id] for concept in ordered if key[concept.id] is not None]
    order = [
        name for index, name in enumerate(sequence)
        if index == 0 or name != sequence[index - 1]
    ]
    accepted_set = set(accepted)
    covered = covered_links(data, concepts, decisions)
    reachable = accepted_set | set(pending)
    missing_required = [edge for edge in required if edge not in accepted_set]
    unreachable = [edge for edge in required if edge not in reachable]
    rules = Counter(
        (row.get("evidence") or {}).get("rule", "none")
        for row in decisions if row["verdict"] == criteria.ACCEPTED
    )
    return {
        "topic": data["topic_id"],
        "accepted": [list(edge) for edge in accepted],
        "pending": [list(edge) for edge in pending],
        "missing_required": [list(edge) for edge in missing_required],
        "unexpected_missing": [list(edge) for edge in missing_required if edge not in known_missing],
        "gaps_closed": [list(edge) for edge in known_missing if edge in accepted_set],
        "forbidden_accepted": [list(edge) for edge in accepted if forbidden(edge)],
        "extra_accepted": [list(edge) for edge in accepted if edge not in required and not forbidden(edge)],
        "unreachable": [list(edge) for edge in unreachable],
        "reachable_count": len(required) - len(unreachable),
        "covered": covered,
        "covered_count": len(covered),
        "accepted_precision": (
            sum(1 for edge in accepted if edge in implied) / len(accepted) if accepted else None
        ),
        "accepted_by_rule": dict(rules),
        "order": order,
        "order_matches": order == data["expected_order"],
        "kendall_tau": kendall_tau(order, data["expected_order"]),
        "ignored_links": [[key[before], key[after]] for before, after in ignored],
        "unkeyed_concepts": sum(1 for concept in concepts if concept.key is None),
    }


def _keyed_texts(concepts):
    texts = prepare(concepts, embed=embeddings.embed)
    by_key = defaultdict(list)
    for text in texts:
        if getattr(text.concept, "key", None):
            by_key[text.concept.key].append(text)
    return texts, by_key


def gate_loss(data, concepts, calibration):
    """The key's links whose two concepts the relatedness gate keeps apart."""
    _, by_key = _keyed_texts(concepts)
    lost = []
    for before, after in data["required"]:
        closest = max(
            (relatedness(first, second) for first in by_key[before] for second in by_key[after]),
            default=0.0,
        )
        if closest < calibration["related_cutoff"]:
            lost.append([before, after])
    return lost


def clue_accuracy(data, concepts, calibration):
    """For each clue, how many of the key's links it points the right and the wrong way."""
    texts, _ = _keyed_texts(concepts)
    owners = find_term_owners(texts)
    positions = material_positions(concepts)
    required = {tuple(edge) for edge in data["required"]}
    counts = {clue: {"right": 0, "wrong": 0} for clue in CLUES}
    for pair in pair_votes(texts, owners, positions, calibration["related_cutoff"], calibration["meaning_cutoff"]):
        first, second = getattr(pair["first"].concept, "key", None), getattr(pair["second"].concept, "key", None)
        if (first, second) in required:
            truth = 1
        elif (second, first) in required:
            truth = -1
        else:
            continue
        for clue in CLUES:
            vote = pair["votes"][clue]
            if vote:
                counts[clue]["right" if vote == truth else "wrong"] += 1
    return counts


def load_course_gold(first_topic, second_topic):
    """``(map data, [concepts of first, concepts of second])`` from a frozen course fixture."""
    data = json.loads(
        (FIXTURES / f"gold_course_{first_topic}_{second_topic}.json").read_text(encoding="utf-8")
    )
    topics, next_id = [], 1
    for topic_id in data["topics"]:
        concepts = []
        for index, row in enumerate(data["concepts"][str(topic_id)]):
            members = tuple(SimpleNamespace(**member) for member in row["members"])
            text = "\n".join(member.content for member in members)
            concepts.append(SimpleNamespace(
                id=next_id, key=row["key"], title=row["title"], content=text, member_text=text,
                section_title=row["section_title"], kind=row.get("kind", "text"), order=index, members=members,
            ))
            next_id += 1
        topics.append(concepts)
    return data, topics


def course_gold_report(data, topic_concepts, decisions):
    """How cross-topic decisions measure up against a course key (course spec section 7)."""
    key = {concept.id: concept.key for topic in topic_concepts for concept in topic}
    required = {tuple(edge) for edge in data["required"]}

    def keyed(row):
        return key.get(row["prerequisite"].id), key.get(row["dependent"].id)

    accepted = [keyed(row) for row in decisions if row["verdict"] == criteria.ACCEPTED]
    pending = [keyed(row) for row in decisions if row["verdict"] == criteria.PENDING]
    flags = {"right": 0, "wrong": 0}
    for row in decisions:
        if (row.get("evidence") or {}).get("contradicts_outline"):
            flags["right" if keyed(row) in required else "wrong"] += 1
    reachable = required & (set(accepted) | set(pending))
    return {
        "accepted": [list(edge) for edge in accepted],
        "pending": [list(edge) for edge in pending],
        "accepted_precision": (
            sum(1 for edge in accepted if edge in required) / len(accepted) if accepted else None
        ),
        "reachable_count": len(reachable),
        "required_count": len(required),
        "unrelated_accepted": len(accepted) if data.get("unrelated") else 0,
        "outline_flags": flags,
    }


def move_report(data, concepts, decisions, move):
    """How a moved lesson's links came out (v7 spec section 8).

    The moved concepts' key links are right, wrong (accepted reversed), pending
    or missing; ``wrong_way_elsewhere`` is any other key link accepted reversed.
    """
    moved = set(move["concepts"])
    key = {concept.id: concept.key for concept in concepts}
    accepted = set(_edges(decisions, key, criteria.ACCEPTED))
    pending = set(_edges(decisions, key, criteria.PENDING))
    required = [tuple(edge) for edge in data["required"]]
    links = [edge for edge in required if edge[0] in moved or edge[1] in moved]
    right = [edge for edge in links if edge in accepted]
    wrong = [edge for edge in links if (edge[1], edge[0]) in accepted]
    waiting = [edge for edge in links if edge not in right and edge not in wrong
               and (edge in pending or (edge[1], edge[0]) in pending)]
    missing = [edge for edge in links if edge not in right and edge not in wrong and edge not in waiting]
    elsewhere = [edge for edge in required if edge not in links and (edge[1], edge[0]) in accepted]
    return {
        "move": move["number"],
        "topic": move["topic"],
        "links": len(links),
        "right_way": [list(edge) for edge in right],
        "wrong_way": [list(edge) for edge in wrong],
        "pending": [list(edge) for edge in waiting],
        "missing": [list(edge) for edge in missing],
        "wrong_way_elsewhere": [[after, before] for before, after in elsewhere],
    }


def vote_accuracy(data, concepts):
    """For each v7 vote, how many of the key's links it points the right way, the wrong way, or not at all."""
    texts = prepare(concepts)
    matrix = build_block_matrix(texts, find_term_owners(texts))
    first_of_key = {}
    for text in texts:
        if getattr(text.concept, "key", None):
            first_of_key.setdefault(text.concept.key, text)
    counts = {vote: {"right": 0, "wrong": 0, "silent": 0} for vote in VOTES}
    for before, after in data["required"]:
        votes, _ = cast_votes(first_of_key[before], first_of_key[after], matrix)
        for vote, value in votes.items():
            counts[vote]["right" if value == 1 else "wrong" if value == -1 else "silent"] += 1
    return counts


def course_shortlist_hits(data, topic_concepts, decisions):
    """Dependents with a key prerequisite, and how many were offered one (accepted or pending)."""
    key = {concept.id: concept.key for topic in topic_concepts for concept in topic}
    required = {tuple(edge) for edge in data["required"]}
    offered = {
        (key.get(row["prerequisite"].id), key.get(row["dependent"].id))
        for row in decisions if row["verdict"] in (criteria.ACCEPTED, criteria.PENDING)
    }
    dependents = {after for _, after in required}
    hit = {after for before, after in required if (before, after) in offered}
    return {"dependents": len(dependents), "hit": len(hit), "offered": len(offered)}
