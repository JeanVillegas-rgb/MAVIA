"""What evidence, if any, says one learning object is a prerequisite of another.

A prerequisite edge here asserts: *understanding A is expected to support
successful comprehension or assessment of B* -- broad enough that a learner who
fails B can sensibly be sent back to A, narrow enough to exclude "these two
passages are about the same topic".

Evidence is ordinal, not weighted. There is no labelled data with which to
justify numbers, and the previous design's weights made its scores look more
discriminating than they were.

* **Strong** evidence may create an edge alone.
* **Medium** evidence needs corroboration from a *different* medium type.
* **Weak** evidence never creates an edge. It generates candidates and explains
  them, nothing more. This is the biggest change from the previous design,
  where a plain textual mention produced 88% of all edges.

See docs/superpowers/specs/2026-09-10-prerequisite-redesign-design.md.
"""

import re
from dataclasses import dataclass
from typing import Any

from .concepts import resolve_concept, resolve_concepts, taught_concepts
from .text_signals import (
    MIN_SHARED_TERMS_FOR_COOCCURRENCE,
    STOP_WORDS,
    content_terms,
    definition_subject,
    distinctive_document_frequency_limit,
    document_frequencies,
    mentions,
    normalize,
    scan_positions,
    searchable_text,
    strip_part_suffix,
)

STRONG = "strong"
MEDIUM = "medium"
WEAK = "weak"

# The author speaking directly about what must be understood first. Rare -- two
# chunks in 67 across the sample material -- but it outranks everything else,
# including the suppressors, because it is a pedagogical judgement rather than
# an inference of ours.
_EXPLICIT = re.compile(
    r"\b(should come first"
    r"|before (you |we )?(can )?(understand|learn)"
    r"|to understand"
    r"|you (should|must) (know|understand)"
    r"|after (learning|understanding)"
    r"|builds? on"
    r"|based on)\b",
    re.I,
)

# "B is a state of A", "B is one of the forms of A".
_IS_A = re.compile(
    r"\b(?:is|are)\s+(?:a|an|one of the)\s+[a-z ]{0,24}?"
    r"(?:type|kind|form|state|category|class)s?\s+of\s+([a-z][a-z ]{1,30})",
    re.I,
)

# A mention inside a contrastive clause says what something is *not* like.
# "each state", "these states", "the three states" -- a group referred to
# collectively instead of by name.
_ANAPHORIC = re.compile(r"\b(each|these|those|the (?:three|two|four)|all(?: the)?)\s+\w+\b", re.I)


def _names_any(normalized_sentence, ctx):
    return any(
        concept and mentions(normalized_sentence, concept)
        for concept in ctx.taught
    )


_CONTRAST = re.compile(r"\b(while|whereas|unlike|but not|although|however)\b", re.I)

# Chunks whose job is to gather several concepts together.
_AGGREGATION = re.compile(r"\b(compar|contrast|summary|differences?|versus|review)\w*\b", re.I)


@dataclass(frozen=True)
class Evidence:
    type: str
    tier: str
    detail: Any = None


class MaterialContext:
    """Everything the pair-level rules need, computed once per material."""

    def __init__(self, material, learning_objects):
        self.objects = list(learning_objects)
        self.by_id = {item.id: item for item in self.objects}
        self.concepts = resolve_concepts(self.objects)
        self.taught = taught_concepts(self.objects)
        self.positions = scan_positions(self.objects)
        self.content = {item.id: searchable_text(item) for item in self.objects}
        self.terms = {
            item.id: content_terms(f"{item.title} {item.content}") for item in self.objects
        }
        self.frequencies = document_frequencies(self.terms)
        self.distinctive_limit = distinctive_document_frequency_limit(len(self.objects))
        self.scope_concept, self.subject_ids = _outline_framing(material, self)
        self.sibling_ids = _sibling_ids(self)


def build_context(material, learning_objects):
    return MaterialContext(material, learning_objects)


def _title_terms(title):
    return {
        word for word in normalize(strip_part_suffix(title or "")).split()
        if word not in STOP_WORDS and len(word) > 2
    }


def _outline_framing(material, ctx):
    """S3 -- structure a teacher confirmed, rather than wording an author chose.

    The module names the broader concept ("Properties of Matter"); the topic
    enumerates its subjects ("Solid, Liquid and Gas"). This is the only
    expert-authored hierarchy that survives PDF extraction intact, and it is
    what lets `Matter -> Solid` be recovered from a lesson that never writes
    "a solid is a state of matter".
    """
    node = getattr(material, "outline_node", None)
    if node is None:
        return None, set()
    module = getattr(node, "parent", None)

    topic_terms = _title_terms(node.title)
    module_terms = _title_terms(module.title) if module is not None else set()

    subject_ids = {
        item.id for item in ctx.objects
        if ctx.concepts.get(item.id) and _title_terms(item.title) & topic_terms
    }

    scope_concept = None
    broader = module_terms - topic_terms
    if broader:
        candidates = [
            item for item in ctx.objects
            if ctx.concepts.get(item.id) and _title_terms(item.title) & broader
        ]
        # Prefer a chunk that actually defines the concept, then the earliest.
        candidates.sort(key=lambda item: (definition_subject(item.content) is None,
                                          ctx.positions[item.id]))
        if candidates:
            scope_concept = ctx.concepts[candidates[0].id]
            subject_ids -= {candidates[0].id}

    return scope_concept, subject_ids


def _sibling_ids(ctx):
    """Peers: concepts the topic title enumerates together, or sharing an is-a
    parent. Neither is a prerequisite of the other."""
    groups = []
    if ctx.subject_ids:
        groups.append(set(ctx.subject_ids))

    by_parent = {}
    for item in ctx.objects:
        parent = _is_a_parent(item, ctx)
        if parent:
            by_parent.setdefault(parent, set()).add(item.id)
    groups.extend(members for members in by_parent.values() if len(members) > 1)

    siblings = {item.id: set() for item in ctx.objects}
    for group in groups:
        for member in group:
            siblings[member].update(group - {member})
    return siblings


def _is_a_parent(item, ctx):
    """The concept this chunk declares itself a kind of, if any."""
    match = _IS_A.search(item.content or "")
    if not match:
        return None
    parent = normalize(match.group(1))
    # The subject must be this chunk's own concept, or "there is a lot of space
    # between particles" reads as `gas is-a space`.
    own = ctx.concepts.get(item.id)
    if not own or not mentions(normalize(item.content[:match.start() + 1]), own):
        return None
    for candidate in sorted(ctx.taught, key=len, reverse=True):
        if candidate and mentions(parent, candidate):
            return candidate
    return parent


def _mention_records(a, b, ctx):
    """How B refers to A's concept, and whether each mention is contrastive.

    Contrast is scoped to the clause, not the sentence. "Liquids and gases can
    flow, while solids normally do not" contrasts *solids* only -- liquids and
    gases are its subject. Marking the whole sentence contrastive suppressed all
    three, which left the Flow passage with no prerequisites at all.
    """
    concept = ctx.concepts.get(a.id)
    if not concept:
        return []

    records = []
    for sentence in re.split(r"(?<=[.!?])\s+", b.content or ""):
        marker = _CONTRAST.search(sentence)
        if not marker:
            if mentions(normalize(sentence), concept):
                records.append(False)
            continue
        # Before the marker the sentence asserts; after it, it contrasts.
        before, after = sentence[:marker.start()], sentence[marker.end():]
        if mentions(normalize(before), concept):
            records.append(False)
        if mentions(normalize(after), concept):
            records.append(True)
    return records


def gather(a, b, ctx):
    """Evidence that ``a`` is a prerequisite of ``b``, split by tier."""
    strong, medium, weak = [], [], []
    a_concept = ctx.concepts.get(a.id)
    b_concept = ctx.concepts.get(b.id)

    # --- S1 explicit dependency -----------------------------------------
    # The statement must NAME the concept it depends on, in the same sentence.
    # Without that scope, one sentence like "understanding each state should
    # come first" made every concept in the material a prerequisite of that
    # chunk -- 46 edges from two sentences, measured.
    if a_concept:
        for sentence in re.split(r"(?<=[.!?])\s+", b.content or ""):
            marker = _EXPLICIT.search(sentence)
            if not marker:
                continue
            normalized = normalize(sentence)
            if mentions(normalized, a_concept):
                strong.append(Evidence("explicit_dependency", STRONG, marker.group(0)))
                break
            # These statements refer to their subjects collectively rather than
            # by name -- "understanding each state should come first", "to
            # understand the difference between these states". Requiring a
            # literal name silences them entirely, which was measured. When the
            # sentence names no taught concept at all but does refer to a group,
            # it is read as applying to the concepts the confirmed topic title
            # enumerates -- and to nothing else.
            if _ANAPHORIC.search(sentence) and not _names_any(normalized, ctx):
                if a.id in ctx.subject_ids:
                    strong.append(Evidence("explicit_dependency", STRONG,
                                           f"{marker.group(0)} (collective reference)"))
                    break

    # --- S2 taxonomic is-a, parent taught here ---------------------------
    parent = _is_a_parent(b, ctx)
    is_a_fired = bool(parent and a_concept and parent == a_concept and parent in ctx.taught)
    if is_a_fired:
        strong.append(Evidence("is_a", STRONG, parent))

    # --- S3 confirmed scope framing --------------------------------------
    if (ctx.scope_concept
            and a_concept == ctx.scope_concept
            and b.id in ctx.subject_ids
            and ctx.positions[a.id] < ctx.positions[b.id]):
        strong.append(Evidence("scope_framing", STRONG, ctx.scope_concept))

    # --- M1 definitional dependency --------------------------------------
    # Skipped when is-a already fired: the is-a sentence usually *is* the
    # defining sentence, and one observation must not be counted twice.
    if a_concept and not is_a_fired:
        opening = re.split(r"(?<=[.!?])\s+", (b.content or "").strip(), 1)[0]
        if definition_subject(b.content or "") and mentions(normalize(opening), a_concept):
            medium.append(Evidence("definitional", MEDIUM, a_concept))

    records = _mention_records(a, b, ctx)

    # --- S4 aggregation dependency -----------------------------------------
    # A chunk whose author-assigned heading declares it a comparison or summary
    # cannot be understood without the things it treats. That is the chunk's
    # stated function, not an inference about shared topic, which is why it
    # stands alone. Only 5 of 64 chunks in the sample material qualify, so it
    # cannot reintroduce density.
    if records and any(not contrastive for contrastive in records):
        label = f"{b.title} {b.section_title}"
        if _AGGREGATION.search(label):
            strong.append(Evidence("aggregation", STRONG, b.section_title or b.title))

    # --- M3 instantiation --------------------------------------------------
    if a_concept and b_concept and b_concept != a_concept:
        if re.search(rf"\b(is|are)\s+(a|an)\s+{re.escape(a_concept)}\b", b.content or "", re.I):
            medium.append(Evidence("instantiation", MEDIUM, b_concept))

    # --- M4 section progression --------------------------------------------
    section = (a.section_title or "").strip()
    if section and section == (b.section_title or "").strip():
        if ctx.positions[a.id] < ctx.positions[b.id]:
            medium.append(Evidence("section_progression", MEDIUM, section))

    # --- Weak: candidate generation and explanation only --------------------
    if records:
        weak.append(Evidence("mention", WEAK, len(records)))
    shared = {
        term for term in ctx.terms[a.id] & ctx.terms[b.id]
        if ctx.frequencies[term] <= ctx.distinctive_limit
    }
    if len(shared) >= MIN_SHARED_TERMS_FOR_COOCCURRENCE:
        weak.append(Evidence("cooccurrence", WEAK, sorted(shared)[:10]))

    return strong, medium, weak


def suppressed(a, b, ctx, author_says_so=False, strong=()):
    """Strong negative evidence, and what may override it.

    Must be called *after* strong evidence is gathered. An earlier draft applied
    the suppressors first, which made the documented override unreachable and
    put the code at odds with its own specification.

    The overrides differ by what each suppressor is actually claiming:

    * **Same concept** is absolute. No statement can make a concept precede
      itself.
    * **Siblings** and **contrastive mentions** claim there is no dependency at
      all, so only the author saying otherwise (S1) overrides them.
    * **Mutual reference** claims only that the *direction* is ambiguous. Any
      strong signal settles direction -- a chunk whose heading declares it a
      comparison depends on what it compares, whether or not those passages
      mention it back -- so mutual reference yields to all of them.
    """
    a_concept, b_concept = ctx.concepts.get(a.id), ctx.concepts.get(b.id)
    if a_concept and a_concept == b_concept:
        return True

    if author_says_so:
        return False

    if b.id in ctx.sibling_ids.get(a.id, set()):
        return True

    records = _mention_records(a, b, ctx)
    if records and all(records):          # every mention is contrastive
        return True

    if records and _mention_records(b, a, ctx) and not strong:
        return True

    return False


def accept(strong, medium):
    """One strong signal, or two medium signals of *different* types.

    The two-medium rule is an operating decision chosen to favour precision,
    not a derived result. It is transparent, which is its advantage over
    numeric weights; it is not validated.
    """
    if strong:
        return True
    return len({item.type for item in medium}) >= 2
