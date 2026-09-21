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
            kind="text",
            order=index,
            members=members,
        ))
    return data, concepts


def _edges(decisions, key, verdict):
    return sorted({
        (key[row["prerequisite"].id], key[row["dependent"].id])
        for row in decisions
        if row["verdict"] == verdict
    })


def gold_report(data, concepts, decisions):
    key = {concept.id: concept.key for concept in concepts}
    by_key = {concept.key: concept for concept in concepts}
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

    links = [(by_key[before].id, by_key[after].id) for before, after in accepted]
    ordered, _, ignored = order_with_links(concepts, links)
    order = [key[concept.id] for concept in ordered]
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
    }
