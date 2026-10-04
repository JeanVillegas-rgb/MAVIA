"""The closest-match course level (course spec 2026-10-03, section 3).

For a concept of a later topic, the concept of an earlier topic whose lesson
text is closest. It is accepted when the later concept also names it and
shares two of its distinctive words; it is suggested when it stands out from
the later concept's own topic; otherwise there is no link. Titles never rank.
"""

import statistics

from .clues import MIN_SHARED_TERMS, owned_words, term_use
from .fusion import ACCEPTED, PENDING
from .relatedness import relatedness

# Design pairs, 2026-10-03: a closest match this much closer than the later
# concept's own topic-mates was right 11 times in 17, and never linked two
# unrelated topics (spec section 2).
CLOSEST_MARGIN = 0.10


def closest_earlier(earlier, later):
    """``(text, score)``: the concept of ``earlier`` whose lesson text is closest to ``later``.

    ``(None, 0.0)`` when no earlier concept has a sentence; ties keep the earlier topic's order.
    """
    best, best_score = None, 0.0
    for text in earlier:
        if not text.sentences:
            continue
        score = relatedness(text, later)
        if best is None or score > best_score:
            best, best_score = text, score
    return best, best_score


def own_topic_median(later, topic):
    """How close ``later`` is to the other concepts of its own topic (median), or ``None`` with none."""
    scores = [relatedness(other, later) for other in topic if other is not later and other.sentences]
    return float(statistics.median(scores)) if scores else None


def name_sentences(holder, target):
    """How many of the holder's sentences contain every stem of the target's name.

    One title on both ("What I Need to Know" in two lessons) names neither, as in ``name_vote``.
    """
    if not target.name or set(target.name) == set(holder.name):
        return 0
    needed = set(target.name)
    return sum(1 for stems in holder.sentence_terms if needed <= set(stems))


def closest_verdict(closest, later, score, own_median, owners):
    """``(verdict, evidence)`` for ``later`` and its closest earlier concept; ``None`` means no link."""
    named = name_sentences(later, closest)
    shares_words = term_use(later, closest, owners, MIN_SHARED_TERMS) > 0
    margin = None if own_median is None else score - own_median
    if named and shares_words:
        verdict = ACCEPTED
    elif margin is not None and margin >= CLOSEST_MARGIN:
        verdict = PENDING
    else:
        verdict = None
    evidence = {
        "rule": "course-closest",
        "score": round(float(score), 3),
        "relatedness": round(float(score), 3),
        "own_median": None if own_median is None else round(own_median, 3),
        "margin": None if margin is None else round(float(margin), 3),
        "name_sentences": named,
        "shared_words": owned_words(later, closest, owners),
        "confirmed": verdict == ACCEPTED,
        "contradicts_outline": False,
        "semantic": True,
    }
    return verdict, evidence
