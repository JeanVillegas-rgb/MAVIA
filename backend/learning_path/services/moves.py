"""Misplaced-concept copies of gold topics, for the v7 direction test.

Spec: docs/superpowers/specs/2026-10-01-learning-path-v7-direction-votes-design.md,
section 8. A move takes every block of some concepts and puts it where the
lesson clearly should not have it; the answer key stays the same, so a rule
that reads more than the order can put the concept back.
"""

import json
from types import SimpleNamespace

from .gold import FIXTURES

MOVES_FILE = FIXTURES / "direction_moves.json"


def load_moves(topic_id=None):
    moves = json.loads(MOVES_FILE.read_text(encoding="utf-8"))["moves"]
    if topic_id is None:
        return moves
    return [move for move in moves if str(move["topic"]) == str(topic_id)]


def _new_spots(anchor, count):
    """``count`` positions strictly between ``anchor`` and ``anchor + 1``, in order."""
    return [anchor + (index + 1) / (count + 1) for index in range(count)]


def apply_move(concepts, move):
    """A copy of ``concepts`` with the move applied, in the merged order and in every PDF."""
    keys = {item.key for item in concepts}
    moving_keys = set(move["concepts"])
    target = move.get("target")
    if not moving_keys <= keys or (move["place"] in ("before", "after") and target not in keys):
        raise ValueError(f"move {move.get('number')}: {sorted(moving_keys - keys) or target} is not in this topic")

    moving = [item for item in concepts if item.key in moving_keys]
    staying = [item for item in concepts if item.key not in moving_keys]
    if move["place"] == "start":
        merged = moving + staying
    elif move["place"] == "end":
        merged = staying + moving
    else:
        spots = [index for index, item in enumerate(staying) if item.key == target]
        cut = spots[0] if move["place"] == "before" else spots[-1] + 1
        merged = staying[:cut] + moving + staying[cut:]

    new_order = {}
    materials = {chunk.material_id for item in concepts for chunk in item.members}
    for material in materials:
        chunks = [chunk for item in moving for chunk in item.members if chunk.material_id == material]
        others = [chunk for item in staying for chunk in item.members if chunk.material_id == material]
        targets = [chunk.order for item in staying if item.key == target
                   for chunk in item.members if chunk.material_id == material]
        if not chunks or not others:
            continue
        if move["place"] == "start":
            anchor = min(chunk.order for chunk in others) - 1
        elif move["place"] == "end":
            anchor = max(chunk.order for chunk in others)
        elif not targets:
            continue  # this PDF does not teach the target: leave the concept where it is
        elif move["place"] == "before":
            anchor = min(targets) - 1
        else:
            anchor = max(targets)
        for chunk, spot in zip(chunks, _new_spots(anchor, len(chunks))):
            new_order[id(chunk)] = spot

    return [
        SimpleNamespace(**{
            **vars(item),
            "order": position,
            "members": tuple(
                SimpleNamespace(**{**vars(chunk), "order": new_order.get(id(chunk), chunk.order)})
                for chunk in item.members
            ),
        })
        for position, item in enumerate(merged)
    ]
