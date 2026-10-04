"""Display titles that tell a topic's concepts apart.

Two PDFs routinely split one heading into several passages -- the definition,
the properties, the examples -- and grouping rightly keeps those apart. Every
one of them is still called after the heading, so a topic ends up with three
concepts named "Solid". The stored label is left alone (grouping, renaming and
the learning path all read it); only the title shown is made distinct:

* A label a teacher typed is shown exactly as typed.
* A label that only numbers a figure ("Figure 1") is replaced by the figure's
  own caption, found by the caption detection extraction already uses.
* Titles still shared are numbered in reading order: "Solid I", "Solid II".
"""

from collections import defaultdict
import re

# A label that only numbers a figure: every PDF has a "Figure 1".
_NUMBERED_FIGURE_LABEL = re.compile(
    r"\s*(?:figure|fig|table|diagram|illustration|image)\.?\s*\d+[a-z]?\s*[.:]?\s*",
    re.IGNORECASE,
)


def _caption_name(members):
    """The first member figure's own caption, for a concept named "Figure 1"."""
    from .semantic_grouping import figure_caption, recorded_caption

    for item in members:
        caption = figure_caption(recorded_caption(item))
        if caption:
            return caption
    return ""


# A name this long is a sentence. A passage with no heading is titled by its
# first sentence ("Matter is anything that has mass and takes up space"), and
# when its PDF is the concept's Normal that sentence names the whole concept
# -- even though another PDF headed the same passage "Matter".
_MAX_NAME_WORDS = 6


def _short_member_name(members):
    """The short name the concept's own passages are headed with, or ``""``.

    Titles first, then the sections they sit under; among several, the one
    most passages share, then the shortest.
    """
    from collections import Counter

    from course.version_assignment import clean_group_label

    for field in ("title", "section_title"):
        names = [
            clean_group_label(getattr(item, field, "") or "")
            for item in members
        ]
        names = [name for name in names if name and len(name.split()) <= _MAX_NAME_WORDS]
        if names:
            counts = Counter(name.casefold() for name in names)
            return min(names, key=lambda name: (-counts[name.casefold()], len(name)))
    return ""


def _only_figures(members):
    return bool(members) and all(getattr(item, "kind", "") == "image" for item in members)


def _roman(number):
    numerals = ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
    if not 0 < number < 40:
        return str(number)
    out = ""
    for value, numeral in numerals:
        while number >= value:
            out += numeral
            number -= value
    return out


def display_titles(entries):
    """``{group id: title}`` for one topic's concepts.

    ``entries`` is ``[(group, members, position)]``; ``position`` sorts the
    concepts in reading order.
    """
    composed = {}
    locked = set()
    for group, members, _position in entries:
        base = (group.label or "").strip() or next(
            ((item.title or "").strip() for item in members if (item.title or "").strip()),
            "Untitled concept",
        )
        if (group.version_selection or {}).get("label_locked"):
            locked.add(group.id)
        elif _NUMBERED_FIGURE_LABEL.fullmatch(base) or (
            _only_figures(members) and len(base.split()) > _MAX_NAME_WORDS
        ):
            # A figure concept is named by its printed caption. Its label is
            # otherwise "Figure 1" or, once narrated, the narration's first
            # sentence ("The image shows a flower with ...").
            base = _caption_name(members) or base
        elif len(base.split()) > _MAX_NAME_WORDS:
            base = _short_member_name(members) or base
        composed[group.id] = base

    sharing = defaultdict(list)
    for group, _members, position in entries:
        if group.id not in locked:
            sharing[composed[group.id].casefold()].append((position, group.id))
    titles = dict(composed)
    for same in sharing.values():
        if len(same) < 2:
            continue
        for number, (_position, group_id) in enumerate(sorted(same), start=1):
            titles[group_id] = f"{composed[group_id]} {_roman(number)}"
    return titles
