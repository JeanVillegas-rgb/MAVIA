"""How the pipelines print to the server terminal.

Every pipeline line has the same shape, so a run can be followed by eye and
any line traced back to the database:

    14:02:31  [Grouping] "Particles in solids" -> "States of matter"  49% similar -> sent to teacher review  (object 196 -> 241, 3.0s)

a stage tag, what is being worked on by name, what happened in plain words,
and the ids and timing in brackets at the end. Warnings and errors say so up
front, since those are the lines a reader is scanning for.
"""

import logging
from time import perf_counter


class ConsoleFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s  %(message)s", datefmt="%H:%M:%S")

    def format(self, record):
        line = super().format(record)
        if record.levelno >= logging.ERROR:
            return line.replace("  ", "  ERROR  ", 1)
        if record.levelno >= logging.WARNING:
            return line.replace("  ", "  WARNING  ", 1)
        return line


def name(text, limit=60):
    """A title in quotes, cut short so one line stays one line."""
    text = " ".join(str(text or "untitled").split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return f'"{text}"'


def took(started):
    """Seconds since a perf_counter() reading, as "3.0s"."""
    return f"{perf_counter() - started:.1f}s"


def percent(score):
    """A 0-1 similarity as a whole percentage."""
    return f"{round(float(score) * 100)}%"
