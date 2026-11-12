"""What the learning path prints at each handoff, so a run can be followed in the terminal."""

import logging

from config.console import name

from .clues import MIN_SHARED_TERMS, SIGNIFICANT_G2
from .concept_text import terms
from .fusion import ACCEPTED, DISAGREE, NONE_SHARED, PENDING

logger = logging.getLogger(__name__)

HELD_BACK = {
    "reverse_name": "the earlier one names the later one more than the reverse",
    "pdfs_disagree": "the PDFs disagree on which comes first",
    "backward_only": "only the earlier one refers to the later one",
    "weak_terms": "the only evidence is single shared words",
}

DIRECTION_FROM = {
    "pdf_order": "the order the PDFs teach them in",
    "merged_order": "the merged order of the PDFs",
    "figure": "the figure, which refers to the other concept and so comes after it",
    "name": "the names clue",
}


def _say(*lines):
    for line in lines:
        logger.debug("[Learning path] %s", line)


def _own_headings(text):
    return {
        (getattr(member, "section_title", "") or "").strip().casefold()
        for member in getattr(text.concept, "members", None) or ()
    } - {""}


def log_concepts(texts, owners):
    """Steps 1 and 2: the topic's concepts, then the words each one owns."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    _say(
        "",
        "THE LEARNING PATH decides which concepts must be learned before which, then puts them in that order.",
        "",
        f"STEP 1 of 4: the concepts ({len(texts)}), in the order the PDFs teach them",
    )
    for position, text in enumerate(texts, start=1):
        own = _own_headings(text)
        _say(
            f"  {position}. {name(text.concept.title)}",
            f"       name, as word stems:       {', '.join(text.name) or '-'}",
            f"       also named by its parts:   {', '.join(title for title, _ in text.part_names) or '-'}",
            f"       its own heading:           {', '.join(sorted(own)) or '-'}",
            f"       sentences:                 {len(text.sentences)}",
            f"       taught in PDFs:            {', '.join(str(pdf) for pdf in sorted(text.materials)) or '-'}",
        )

    owned = {}
    for stem, owner in owners.items():
        owned.setdefault(owner, []).append(stem)
    _say(
        "",
        "STEP 2 of 4: owned words. A word belongs to a concept when two or more concepts use it, but this one uses it",
        f"  significantly more than the rest of the topic (Dunning's G2 >= {SIGNIFICANT_G2}). Owned words show which",
        "  concept a passage is talking about, even when it never says the concept's name.",
    )
    for text in texts:
        words = sorted(text.spelling.get(stem, stem) for stem in owned.get(text.id, []))
        _say(f"  {name(text.concept.title)}: {', '.join(words) or '-'}")

    pairs = len(texts) * (len(texts) - 1) // 2
    _say(
        "",
        f"STEP 3 of 4: every pair of concepts ({pairs}). For each pair the clues are read in this order:",
        "  1. same section  both sit under one heading that names neither: taught side by side, no link, stop here",
        "  2. names         how often each one's sentences name the other",
        f"  3. owned words   how often each one's passages use {MIN_SHARED_TERMS}+ of the other's owned words",
        "  4. single words  the same with just 1 owned word (weak, used only when nothing else refers)",
        "  5. heading       one is printed under a heading that names the other: that heading decides the link",
        "  6. PDF order     which one the PDFs teach first; the later one should refer back to the earlier one",
    )


def _of(share, total):
    return f"{round(share * total)} of {total}"


def _names_section(holder, target):
    """A heading the holder sits under that names the target."""
    for section in sorted(holder.sections):
        if target.name and set(target.name) <= set(terms(section)):
            return f'"{section}"'
    return "a heading"


def _shared_section(first, second):
    """A heading both sit under that names neither."""
    for section in sorted(first.sections & second.sections):
        stems = set(terms(section))
        if not (first.name and set(first.name) <= stems) and not (second.name and set(second.name) <= stems):
            return f'"{section}"'
    return "one heading"


def _heading_line(earlier, later, facts):
    first, second = name(earlier.concept.title), name(later.concept.title)
    if facts.heading > 0:
        return f"{second} sits under {_names_section(later, earlier)}, which names {first} (+1)"
    if facts.heading < 0:
        return f"{first} sits under {_names_section(earlier, later)}, which names {second} (-1)"
    return "neither sits under a heading that names the other (+0)"


def _order_line(earlier, later, facts):
    first = name(earlier.concept.title)
    both = len(earlier.materials & later.materials)
    if facts.pdf_order == NONE_SHARED:
        return f"no PDF teaches both; {first} comes first only in the merged order of all PDFs"
    if facts.pdf_order == DISAGREE:
        return f"the {both} PDFs that teach both disagree on which comes first"
    if both == 1:
        return f"the one PDF that teaches both puts {first} first"
    if both == 2:
        return f"both PDFs teach the two, and both put {first} first"
    return f"all {both} PDFs that teach the two put {first} first"


def _verdict_lines(earlier, later, facts, decision):
    first, second = name(earlier.concept.title), name(later.concept.title)
    if decision.verdict not in (ACCEPTED, PENDING):
        return [
            "    => NO LINK",
            "       why: neither text refers to the other (no names, no owned words, not even single words),",
            "            no heading links them, and no two PDFs agree on an order",
        ]
    before, after = (first, second) if decision.direction > 0 else (second, first)
    if decision.verdict == ACCEPTED:
        if decision.direction_from == "heading":
            why = "decided by the heading"
        else:
            why = f"{second} refers back to {first} and nothing contradicts it, so {DIRECTION_FROM[decision.direction_from]} stands"
        return [f"    => ACCEPTED: learn {before} before {after}", f"       why: {why}"]
    lines = [f"    => PENDING (the teacher decides): learn {before} before {after}"]
    if decision.direction_from == "pdf_agreement":
        return lines + ["       why: the texts do not refer to each other, but two or more PDFs agree on this order [pdf_agreement]"]
    return lines + [
        f"       direction from: {DIRECTION_FROM[decision.direction_from]}",
        *(f"       held back: {HELD_BACK[code]} [{code}]" for code in decision.contradictions),
    ]


def log_pair(earlier, later, facts, decision):
    """One pair: each clue on its own line, in the order they are read, then the verdict and why."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    first, second = name(earlier.concept.title), name(later.concept.title)
    placed = "the merged order" if facts.pdf_order in (NONE_SHARED, DISAGREE) else "the PDFs"
    lines = ["", f"  {first} and {second}   ({first} comes first in {placed})"]
    if facts.parallel:
        _say(
            *lines,
            f"    1. same section  YES: both sit under {_shared_section(earlier, later)}, which names neither",
            "    => NO LINK",
            "       why: they are taught side by side, so neither is learned first; the other clues are not read",
        )
        return
    later_passages = len([passage for passage in later.passages if passage])
    earlier_passages = len([passage for passage in earlier.passages if passage])
    _say(
        *lines,
        "    1. same section  no",
        f"    2. names         {second} names {first} in {_of(facts.later_names_earlier, len(later.sentences))} sentences;"
        f"  {first} names {second} in {_of(facts.earlier_names_later, len(earlier.sentences))}",
        f"    3. owned words   {second} uses {MIN_SHARED_TERMS}+ of {first}'s in {_of(facts.later_uses_earlier_terms, later_passages)} passages;"
        f"  {first} uses {MIN_SHARED_TERMS}+ of {second}'s in {_of(facts.earlier_uses_later_terms, earlier_passages)}",
        f"    4. single words  {second} uses 1+ of {first}'s in {_of(facts.later_uses_earlier_word, later_passages)} passages;"
        f"  {first} uses 1+ of {second}'s in {_of(facts.earlier_uses_later_word, earlier_passages)}",
        f"    5. heading       {_heading_line(earlier, later, facts)}",
        f"    6. PDF order     {_order_line(earlier, later, facts)}",
        *_verdict_lines(earlier, later, facts, decision),
    )


def log_order(ordered, depth, ignored, links=()):
    """Step 4: Kahn's order, with what each concept needs first and its level."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    titles = {concept.id: concept.title for concept in ordered}
    needs = {}
    for before, after in links:
        needs.setdefault(after, []).append(before)
    position = {concept.id: index for index, concept in enumerate(ordered)}
    _say(
        "",
        f"STEP 4 of 4: the order, from the {len(links)} links that shape it (accepted, or approved by a teacher;",
        "  links waiting for the teacher are not used). A concept is placed once everything it needs is placed;",
        "  when several are ready, the one the PDFs teach first goes first. Level = how many prerequisites deep it is.",
    )
    for index, concept in enumerate(ordered, start=1):
        first = sorted(needs.get(concept.id, []), key=position.get)
        _say(
            f"  {index}. {name(concept.title, 40):<44} level {depth[concept.id]}   needs: "
            + (", ".join(name(titles[before], 34) for before in first) or "nothing")
        )
    if ignored:
        _say(
            "  links dropped to break a loop (the weakest link of each loop): "
            + ", ".join(f"{name(titles[before], 34)} -> {name(titles[after], 34)}" for before, after in ignored)
        )
