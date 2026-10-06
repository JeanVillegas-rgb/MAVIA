"""Similarity cutoffs and clue agreement, learned once and stored for the whole group.

Weights are not recomputed when a screen opens, so a new upload never quietly
changes another topic's links. The stored file dates from 2026-09-30; its cutoffs
only fill the recorded relatedness and meaning numbers, never a verdict.
"""

import copy
import json
import logging
from datetime import date
from pathlib import Path

from .clues import find_term_owners, meaning_cutoff, pair_votes
from .embeddings import MODEL, REVISION
from .fusion import CLUES, learn_weights
from .relatedness import related_cutoff

logger = logging.getLogger(__name__)

CALIBRATION = Path(__file__).resolve().parent.parent / "calibration" / "weights.json"

# Used until the command has been run. Cutoffs measured 2026-09-30 on topics
# 340 x 357 (150 concept pairs, 2005 sentence matches).
DEFAULTS = {
    "weights": {"name": 1.0, "terms": 1.0, "meaning": 1.0, "heading": 1.0, "order": 1.0},
    "related_cutoff": 0.25,
    "meaning_cutoff": 0.30,
    "source": "defaults",
}


def load_calibration(path=CALIBRATION):
    """The stored calibration, or the defaults when the file is missing or unreadable."""
    try:
        stored = json.loads(Path(path).read_text(encoding="utf-8"))
        return {
            "weights": {clue: float(stored["weights"][clue]) for clue in CLUES},
            "related_cutoff": float(stored["related_cutoff"]),
            "meaning_cutoff": float(stored["meaning_cutoff"]),
            "source": str(path),
        }
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("[Learning path] calibration file %s ignored, using defaults: %s", path, exc)
        return copy.deepcopy(DEFAULTS)


def calibrate(texts_by_topic, positions_by_topic, topics, unrelated_topic_pairs):
    """Cutoffs from topics in different subjects, then weights from the related pairs of ``topics``."""
    unrelated = [
        (first, second)
        for first_topic, second_topic in unrelated_topic_pairs
        for first in texts_by_topic[first_topic]
        for second in texts_by_topic[second_topic]
    ]
    related = related_cutoff(unrelated)
    meaning = meaning_cutoff(unrelated)
    if related is None or meaning is None:
        raise ValueError("the unrelated topics have no sentences to measure")
    rows = []
    for topic in topics:
        texts = texts_by_topic[topic]
        owners = find_term_owners(texts)
        rows.extend(
            pair["votes"]
            for pair in pair_votes(texts, owners, positions_by_topic[topic], related, meaning)
            if pair["related"]
        )
    weights, agreement = learn_weights(rows)
    return {
        "model": MODEL,
        "revision": REVISION,
        "date": date.today().isoformat(),
        "topics": sorted(topics),
        "unrelated": [list(pair) for pair in unrelated_topic_pairs],
        "related_cutoff": round(related, 4),
        "meaning_cutoff": round(meaning, 4),
        "weights": {clue: round(weight, 4) for clue, weight in weights.items()},
        "agreement": {clue: round(value, 4) for clue, value in agreement.items()},
        "pairs": len(rows),
    }
