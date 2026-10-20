"""One call that both records a trace event and prints it."""

import logging

from django.db.models import Max

from .models import GenerationEvent

logger = logging.getLogger(__name__)


def run_tracer(run, *, label=None):
    """Return record(event_type, message, **data) bound to one run."""
    prefix = label or run.get_kind_display()
    highest = run.events.aggregate(highest=Max("seq"))["highest"] or 0
    seq = {"n": highest}

    def record(event_type, message="", **data):
        seq["n"] += 1
        GenerationEvent.objects.create(
            run=run,
            seq=seq["n"],
            event_type=event_type,
            message=message,
            data=data or None,
        )
        logger.log(
            _level(event_type, data),
            "[%s] run %s  %s%s",
            prefix,
            run.id,
            message or event_type,
            _progress_suffix(data),
        )
        return seq["n"]

    return record


def _level(event_type, data):
    """Problems as warnings; the start of each item only at DEBUG."""
    if event_type == "error" or event_type.endswith("_failed"):
        return logging.WARNING
    if event_type.endswith("_started") and data.get("index") is not None:
        return logging.DEBUG
    return logging.INFO


def _progress_suffix(data):
    """" (3/12)" when the caller gave a position."""
    index, total = data.get("index"), data.get("total")
    if index is None or total is None:
        return ""
    return f"  ({index} of {total})"
