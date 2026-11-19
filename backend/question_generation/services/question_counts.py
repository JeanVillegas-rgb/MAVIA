"""How many questions a concept gets: per tier and format, the minimum, and the Bloom levels that are never asked."""

from django.conf import settings


def thinking_order_counts():
    """Target questions per thinking order, read from settings at call time."""
    return {"LOT": settings.QUESTION_COUNT_LOT, "HOT": settings.QUESTION_COUNT_HOT}


def question_minimum():
    """LOT and HOT counts every concept needs before the Questions step is done."""
    return {"LOT": settings.QUESTION_MIN_LOT, "HOT": settings.QUESTION_MIN_HOT}


def _split(count, weights):
    """Divide count across formats by weight; the remainder goes to the first format."""
    total = sum(weights.values())
    split = {fmt: count * weight // total for fmt, weight in weights.items()}
    first = next(iter(weights))
    split[first] += count - sum(split.values())
    return {fmt: n for fmt, n in split.items() if n}


_COUNTS = thinking_order_counts()
_FORMAT_WEIGHTS = {"MCQ": settings.QUESTION_FORMAT_WEIGHT_MCQ, "TF": settings.QUESTION_FORMAT_WEIGHT_TF}
QUESTION_DISTRIBUTION = {
    "LOT": {
        "count": _COUNTS["LOT"],
        "format_split": _split(_COUNTS["LOT"], _FORMAT_WEIGHTS),
    },
    "HOT": {
        "count": _COUNTS["HOT"],
        "format_split": _split(_COUNTS["HOT"], _FORMAT_WEIGHTS),
    },
}

UNASSESSABLE_BLOOM_LEVELS = {"create"}
