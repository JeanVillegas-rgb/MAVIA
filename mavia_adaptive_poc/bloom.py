"""Bloom's Taxonomy tier helpers (3-level, BKT-coupled) — Box 6."""

TIER_NAMES = {1: "Remember", 2: "Understand", 3: "Apply"}
QUESTION_TYPE = {1: "true_false", 2: "multiple_choice", 3: "multiple_choice"}

ESCALATE_THRESHOLD = 0.70
DEESCALATE_THRESHOLD = 0.40


def route_for_mastery(mastery):
    """Box 7 routing decision from updated BKT mastery."""
    if mastery > ESCALATE_THRESHOLD:
        return "escalate"
    if mastery < DEESCALATE_THRESHOLD:
        return "deescalate"
    return "stagnate"
