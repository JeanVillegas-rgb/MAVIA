"""Similarity cutoffs, measured once and stored for the whole group.

They are not recomputed when a screen opens, so a new upload never quietly
changes another topic's links. The stored file dates from 2026-09-30; within a
topic its cutoffs only fill the recorded relatedness and meaning numbers, never
a verdict. The course level gates a pair on ``related_cutoff``.
"""

import copy
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CALIBRATION = Path(__file__).resolve().parent.parent / "calibration" / "weights.json"

# Used when the file is missing. Measured 2026-09-30 on topics 340 x 357
# (150 concept pairs, 2005 sentence matches).
DEFAULTS = {
    "related_cutoff": 0.25,
    "meaning_cutoff": 0.30,
    "source": "defaults",
}


def load_calibration(path=CALIBRATION):
    """The stored cutoffs, or the defaults when the file is missing or unreadable."""
    try:
        stored = json.loads(Path(path).read_text(encoding="utf-8"))
        return {
            "related_cutoff": float(stored["related_cutoff"]),
            "meaning_cutoff": float(stored["meaning_cutoff"]),
            "source": str(path),
        }
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("[Learning path] calibration file %s ignored, using defaults: %s", path, exc)
        return copy.deepcopy(DEFAULTS)
