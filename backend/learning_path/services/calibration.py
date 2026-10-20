"""Similarity cutoffs, measured once and stored."""

import copy
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CALIBRATION = Path(__file__).resolve().parent.parent / "calibration" / "weights.json"

DEFAULTS = {
    "meaning_cutoff": 0.30,
    "source": "defaults",
}


def load_calibration(path=CALIBRATION):
    """The stored cutoffs, or the defaults when the file is missing or unreadable."""
    try:
        stored = json.loads(Path(path).read_text(encoding="utf-8"))
        return {
            "meaning_cutoff": float(stored["meaning_cutoff"]),
            "source": str(path),
        }
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("[Learning path] calibration file %s ignored, using defaults: %s", path, exc)
        return copy.deepcopy(DEFAULTS)
