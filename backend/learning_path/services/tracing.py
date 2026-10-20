"""What the learning path prints at each handoff, so a run can be followed in the terminal."""

import logging

from config.console import name

from .clues import SIGNIFICANT_G2

logger = logging.getLogger(__name__)


def log_concepts(texts, owners):
    """The topic's concepts with their names, headings and owned words, before any pair is read."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    owned = {}
    for stem, owner in owners.items():
        owned.setdefault(owner, []).append(stem)
    logger.debug("[Learning path] CONCEPTS (%s), in the topic's merged PDF order", len(texts))
    for position, text in enumerate(texts, start=1):
        own = {
            (getattr(member, "section_title", "") or "").strip().casefold()
            for member in getattr(text.concept, "members", None) or ()
        }
        printed = sorted(text.sections - own)
        logger.debug(
            "[Learning path]   %s. %s  name {%s}  parts %s  headings %s%s  sentences %s  PDFs %s",
            position, name(text.concept.title), ", ".join(text.name) or "-",
            [title for title, _ in text.part_names] or "-",
            sorted(own - {""}) or "-",
            f" + printed in {printed}" if printed else "",
            len(text.sentences), sorted(text.materials),
        )
    logger.debug("[Learning path] OWNED WORDS (used by 2+ concepts, significantly more by this one: G2 >= %s)", SIGNIFICANT_G2)
    for text in texts:
        words = sorted(text.spelling.get(stem, stem) for stem in owned.get(text.id, []))
        logger.debug("[Learning path]   %s: %s", name(text.concept.title), ", ".join(words) or "-")
    pairs = len(texts) * (len(texts) - 1) // 2
    logger.debug(
        "[Learning path] PAIRS (%s), each read earlier -> later in the lessons' order; "
        "a clue shows later-refers-to-earlier | earlier-refers-to-later",
        pairs,
    )


def log_pair(earlier, later, facts, decision):
    """One pair's clues and the verdict they gave."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    clues = [
        f"names {facts.later_names_earlier:.2f}|{facts.earlier_names_later:.2f}",
        f"owned terms {facts.later_uses_earlier_terms:.2f}|{facts.earlier_uses_later_terms:.2f}",
        f"single words {facts.later_uses_earlier_word:.2f}|{facts.earlier_uses_later_word:.2f}",
        f"heading {facts.heading:+d}",
        f"PDF order {facts.pdf_order}" + (f", {facts.pdf_agreement:+d} from 2+ PDFs" if facts.pdf_agreement else ""),
    ]
    if facts.parallel:
        clues.append("parallel")
    if decision.verdict in ("accepted", "pending"):
        before, after = (earlier, later) if decision.direction > 0 else (later, earlier)
        outcome = f"{decision.verdict.upper()} {name(before.concept.title)} -> {name(after.concept.title)} via {decision.direction_from}"
        if decision.contradictions:
            outcome += f", contradicted: {', '.join(decision.contradictions)}"
    else:
        outcome = "no link"
    logger.debug(
        "[Learning path]   %s / %s  %s  -> %s",
        name(earlier.concept.title, 34), name(later.concept.title, 34), "  ".join(clues), outcome,
    )


def log_order(ordered, depth, ignored):
    """Kahn's order with each concept's level, and any link dropped to break a loop."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    logger.debug(
        "[Learning path] KAHN ORDER: %s",
        "  ".join(f"{position}. {name(concept.title, 34)} (level {depth[concept.id]})" for position, concept in enumerate(ordered, start=1)),
    )
    if ignored:
        titles = {concept.id: concept.title for concept in ordered}
        logger.debug(
            "[Learning path] links dropped to break a loop: %s",
            ", ".join(f"{name(titles[before], 34)} -> {name(titles[after], 34)}" for before, after in ignored),
        )
