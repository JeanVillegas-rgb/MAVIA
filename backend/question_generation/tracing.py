"""One call that both records a trace event and prints it to the terminal.

Progress reporting had drifted into two halves that could not see each other.
Content extraction wrote `logger.info("[TRACE material ...]")` and nothing else,
so the teacher watching the screen learned nothing; the run pipelines wrote
`GenerationEvent` rows the terminal never showed, so whoever was debugging had
to pick a half and hope. A tracer feeds both from the same call, which is also
the only way they cannot disagree about what happened.

Usage::

    record = run_tracer(run)
    record("extract_started", "Reading the PDF", index=1, total=4)
"""

import logging

from django.db.models import Max

from .models import GenerationEvent

logger = logging.getLogger(__name__)


def run_tracer(run, *, label=None):
    """Return ``record(event_type, message, **data)`` bound to one run.

    The sequence continues from whatever the run already holds rather than
    restarting at zero, so a tracer attached to a run in progress -- a retry, or
    a second stage appending to the same run -- cannot collide with the
    sequence numbers already stored.
    """
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
        logger.info(
            "[%s run %s] %s%s",
            prefix,
            run.id,
            message or event_type,
            _progress_suffix(data),
        )
        return seq["n"]

    return record


def _progress_suffix(data):
    """" (3/12)" when the caller reported a position, nothing otherwise.

    Kept out of the message itself so the same numbers drive the progress bar
    and the console line without either being parsed back out of prose.
    """
    index, total = data.get("index"), data.get("total")
    if index is None or total is None:
        return ""
    return f"  ({index}/{total})"
