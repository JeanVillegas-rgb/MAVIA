"""The three criteria that decide whether one concept precedes another.

Each criterion casts a single vote, they are weighted equally, and two
thresholds turn the tally into an edge, a question for the teacher, or nothing.
See ``learning_path/CRITERIA.md``.

Nothing here writes to the database, and nothing calls a generative model: the
only model involved is the sentence encoder already loaded for grouping, so the
same concepts always produce the same votes.
"""

import math
import re
from collections import Counter, defaultdict

from lessons.services.semantic_grouping import runtime as semantic_runtime

from .concepts import heading_name, is_structural, resolve_concept
from .text_signals import MIN_TERM_LENGTH, STOP_WORDS, mentions, normalize, singular

# ACE's methodology windows the dependent's text rather than embedding it whole,
# so a single sentence referring to another concept is not diluted by a long
# passage around it.
WINDOW_SIZE = 10

# Runtime is O(windows x concepts). A very long concept would otherwise dominate
# a topic's cost for no gain, since its later windows repeat the same subject.
MAX_WINDOWS_PER_CONCEPT = 120

# A ratio needs a denominator. A concept that refers to nothing is maximally
# foundational, and the cap keeps that comparable and sortable instead of
# infinite -- `inf > inf` is False, which would silently drop such a pair.
MIN_OUTBOUND = 1e-6
MAX_IOL = 1e6

# How much more foundational one concept must be before the ratio counts as
# evidence rather than as noise. See `inbound_outbound`.
# Confirmed by the grid (evaluate_gold_paths --grid) on gold topics 62 and 79;
# see docs/learning_path_revision_2026-09-17.md.
MIN_IOL_MARGIN = 0.25

# RefD (Liang et al., 2015): A is a prerequisite of B when B refers to A more
# than A refers to B. A concept is referred to through its *key terms* -- its
# name plus the terms it introduces. The previous measure compared the
# embedding of A's name with B's text, which is similarity, not reference:
# measured on two real lessons, 7 of 8 misses were reversed.

# A term used by more than this share of a topic's concepts belongs to none of
# them ("particles" in a states-of-matter lesson).
# Confirmed by the grid (evaluate_gold_paths --grid) on gold topics 62 and 79;
# see docs/learning_path_revision_2026-09-17.md.
REF_MAX_DF_RATIO = 0.34

# How much more B must refer to A than A to B for the vote to count.
# Calibrated 2026-09-17 on gold topics 62 and 79; see
# docs/learning_path_revision_2026-09-17.md.
REF_MARGIN = 0.0

# ACE (Aytekin & Saygin, 2024) link: a multi-word name phrased differently
# ("change of state") still counts when a text window is this close to it.
# Confirmed by the grid (evaluate_gold_paths --grid) on gold topics 62 and 79;
# see docs/learning_path_revision_2026-09-17.md.
PHRASE_COSINE = 0.80


def _windows(text):
    """Sliding word windows over a concept's text."""
    words = normalize(text).split()
    if not words:
        return []
    if len(words) <= WINDOW_SIZE:
        return [" ".join(words)]
    windows = [
        " ".join(words[start:start + WINDOW_SIZE])
        for start in range(len(words) - WINDOW_SIZE + 1)
    ]
    if len(windows) <= MAX_WINDOWS_PER_CONCEPT:
        return windows
    # Keep an even spread rather than the first N: the end of a passage is as
    # likely to name what it depends on as the beginning.
    stride = len(windows) / MAX_WINDOWS_PER_CONCEPT
    return [windows[int(index * stride)] for index in range(MAX_WINDOWS_PER_CONCEPT)]


def concept_names(concepts):
    """The name each concept can be *referred to by*, or None.

    The name is one of a concept's key terms (see ``key_terms``). A concept
    with no name can still be referred to through the distinctive terms it
    introduces; only a concept owning no key terms at all cannot be.
    """
    return {concept.id: resolve_concept(concept) for concept in concepts}


def concept_text(concept):
    """All of a concept's wording: every member PDF when known, else its own text."""
    return getattr(concept, "member_text", "") or concept.content or ""


def head_words(names):
    """``{concept id: head word}`` for multi-word names, where unambiguous.

    Lessons name a concept by its whole heading ("seed formation") but refer
    to it by its first significant word ("the seed"). A head word that two
    names share points at neither, so it never counts.
    """
    # Every name's head word counts toward ambiguity, single-word names too:
    # beside a "seed" concept, "seed" cannot also point at "seed dispersal".
    candidates = {}
    for concept_id, name in names.items():
        for word in (name or "").split():
            if word not in STOP_WORDS and len(word) >= MIN_TERM_LENGTH:
                candidates[concept_id] = singular(word)
                break
    counts = Counter(candidates.values())
    return {
        concept_id: word for concept_id, word in candidates.items()
        if counts[word] == 1 and len(names[concept_id].split()) >= 2
    }


def contained_in(holder, target_name):
    """True when any of ``holder``'s members sits under a heading naming the target.

    Wang et al. (2016) read textbook section structure as prerequisite
    evidence: a passage under the "Matter" heading builds on Matter even when
    it never says "matter".
    """
    if not target_name:
        return False
    members = getattr(holder, "members", None) or (holder,)
    return any(
        heading_name(getattr(member, "section_title", "") or "") == target_name
        for member in members
    )


def _terms(text):
    return [
        singular(word) for word in normalize(text).split()
        if word not in STOP_WORDS and len(word) >= MIN_TERM_LENGTH
    ]


def key_terms(concepts):
    """``{concept id: {term: weight}}`` -- what a concept can be referred to by.

    A distinctive term (used by at most ``REF_MAX_DF_RATIO`` of the concepts)
    belongs to the concept that *introduces* it -- the earliest concept using
    it -- and weighs its inverse document frequency. Ownership by densest use
    was rejected: "The anther makes pollen" introduces pollen, but Pollination
    uses it more densely, which made Stamen -> Pollination a tie. Words of any concept's name are left
    to the name itself. The name weighs as much as all the concept's terms
    together: naming a concept is the plainest reference to it.
    """
    concepts = list(concepts)
    names = concept_names(concepts)
    name_words = {
        singular(word) for name in names.values() if name for word in name.split()
    }
    counts = {concept.id: Counter(_terms(concept_text(concept))) for concept in concepts}
    order = {concept.id: concept.order for concept in concepts}
    total = len(concepts)
    frequency = Counter(term for bag in counts.values() for term in bag)
    limit = max(1, math.floor(total * REF_MAX_DF_RATIO))

    weights = {concept.id: {} for concept in concepts}
    for term, document_frequency in frequency.items():
        if document_frequency > limit or term in name_words:
            continue
        owner = min(
            (concept_id for concept_id, bag in counts.items() if bag[term]),
            key=lambda concept_id: (order[concept_id], concept_id),
        )
        weights[owner][term] = math.log(total / document_frequency) if total > 1 else 1.0

    for concept in concepts:
        name = names[concept.id]
        if name:
            weights[concept.id][name] = max(sum(weights[concept.id].values()), 1.0)
    return {concept_id: terms for concept_id, terms in weights.items() if terms}


def _phrase_hits(concepts, phrases, texts, runtime_instance):
    """``{(holder id, phrase)}`` -- multi-word names a text says in other words."""
    engine = runtime_instance or semantic_runtime()
    windowed = {concept.id: _windows(concept_text(concept)) for concept in concepts}
    payload = list(phrases)
    for concept in concepts:
        payload.extend(windowed[concept.id])
    vectors = engine.embeddings(payload)
    phrase_vectors = dict(zip(phrases, vectors[:len(phrases)]))

    # Vectors come back normalised, so the dot product below is the cosine.
    hits, cursor = set(), len(phrases)
    for concept in concepts:
        count = len(windowed[concept.id])
        window_vectors = vectors[cursor:cursor + count]
        cursor += count
        for phrase, phrase_vector in phrase_vectors.items():
            if mentions(texts[concept.id], phrase):
                continue
            if any(
                sum(left * right for left, right in zip(phrase_vector, window)) >= PHRASE_COSINE
                for window in window_vectors
            ):
                hits.add((concept.id, phrase))
    return hits


def reference_details(concepts, runtime_instance=None):
    """``(matrix, matched)`` -- ``matrix[(a, b)]`` is how strongly b's text refers to a.

    The score is the weighted share of a's key terms that b's text uses. Only
    concepts with key terms get rows: a concept that owns nothing cannot be
    referred to, so no comparison involving it is a measurement.
    """
    concepts = list(concepts)
    terms = key_terms(concepts)
    names = concept_names(concepts)
    texts = {concept.id: normalize(concept_text(concept)) for concept in concepts}
    phrases = sorted({
        names[concept_id] for concept_id in terms
        if names.get(concept_id) and len(names[concept_id].split()) > 1
    })
    phrase_hits = _phrase_hits(concepts, phrases, texts, runtime_instance) if phrases else set()
    heads = head_words(names)

    matrix, matched = {}, {}
    for target_id, weights in terms.items():
        total = sum(weights.values())
        for holder in concepts:
            if holder.id == target_id:
                continue
            if contained_in(holder, names.get(target_id)):
                matrix[(target_id, holder.id)] = 1.0
                matched[(target_id, holder.id)] = [f"section:{names[target_id]}"]
                continue
            found, score = [], 0.0
            for term, weight in weights.items():
                if mentions(texts[holder.id], term) or (holder.id, term) in phrase_hits:
                    found.append(term)
                    score += weight
                elif (
                    term == names.get(target_id)
                    and target_id in heads
                    and mentions(texts[holder.id], heads[target_id])
                ):
                    found.append(f"head:{heads[target_id]}")
                    score += weight
            matrix[(target_id, holder.id)] = score / total
            matched[(target_id, holder.id)] = sorted(found)
    return matrix, matched


def reference_matrix(concepts, runtime_instance=None):
    """``{(a, b): score}`` -- how strongly b's text refers to a. See ``reference_details``."""
    return reference_details(concepts, runtime_instance)[0]


def inbound_outbound_ratios(concepts, matrix):
    """``{concept id: IOL}`` -- referenced a lot, referring little, is foundational.

    **Only concepts with key terms get a ratio.** Inbound reference is how
    strongly other text refers to a concept's *key terms*, so a concept with
    none has an inbound of zero by construction, not by measurement. Giving it a
    ratio of 0 made every measurable concept look more foundational than it --
    measured on real content, 55 verdicts came from nothing but that. The matrix
    only holds rows for concepts owning key terms (a name or distinctive terms),
    so those are exactly the concepts measured here.
    """
    inbound = defaultdict(float)
    outbound = defaultdict(float)
    for (target_id, holder_id), score in matrix.items():
        inbound[target_id] += score
        outbound[holder_id] += score

    nameable = {target_id for target_id, _ in matrix}
    ratios = {}
    for concept in concepts:
        if concept.id not in nameable:
            continue
        denominator = max(outbound[concept.id], MIN_OUTBOUND)
        ratios[concept.id] = min(inbound[concept.id] / denominator, MAX_IOL)
    return ratios


def temporal_order(a, b):
    """1 when the topic presents ``a`` first.

    ``order`` is already the concept's earliest appearance across the topic's
    materials, so this reads the same whichever file introduced it.
    """
    return 1 if a.order < b.order else 0


def semantic_reference(a, b, matrix):
    """1 when b's text refers to a more than a's refers to b.

    **Both concepts must own key terms for this to mean anything.** A concept
    with neither a name nor a distinctive term cannot be searched for, so its
    side of the comparison is structurally zero. Comparing a measured number
    against one that could never be measured is not evidence of direction; it
    just means the measurable concept always wins. Measured on real data that
    made every such concept a dependent of nearly everything.

    The matrix holds a row for each concept owning key terms (a name or
    distinctive terms), so a missing key is exactly the case where no
    comparison is possible.
    """
    forward_key, backward_key = (a.id, b.id), (b.id, a.id)
    if forward_key not in matrix or backward_key not in matrix:
        return 0

    forward, backward = matrix[forward_key], matrix[backward_key]
    if forward <= 0.0 and backward <= 0.0:
        return 0
    return 1 if forward - backward > REF_MARGIN else 0


def inbound_outbound(a, b, ratios):
    """1 when ``a`` is *clearly* the more foundational of the pair.

    A bare ``>`` makes this a coin toss. Measured on real content the ratios sat
    between 1.60 and 2.12 -- close enough that 2.001 beating 2.000 cast a full
    vote, on every pair, which is what filled the review queue. Requiring a real
    gap means the criterion abstains when it cannot tell the two apart, which is
    the honest answer far more often than not.

    The margin is relative because the ratio is scale-free: what matters is
    being half again as foundational, not being 0.4 higher.

    A concept with no ratio owns no key terms (no name and no distinctive
    term) to be referred to by, so the comparison is not a measurement and no vote is cast -- the same rule
    ``semantic_reference`` applies.
    """
    if a.id not in ratios or b.id not in ratios:
        return 0
    return 1 if ratios[a.id] > ratios[b.id] * (1.0 + MIN_IOL_MARGIN) else 0


def cast_votes(a, b, matrix, ratios, matched=None):
    """Every criterion's vote for "a comes before b", plus the numbers behind it."""
    matched = matched or {}
    forward = matrix.get((a.id, b.id), 0.0)
    backward = matrix.get((b.id, a.id), 0.0)
    return {
        "temporal_order": temporal_order(a, b),
        "semantic_reference": semantic_reference(a, b, matrix),
        "inbound_outbound": inbound_outbound(a, b, ratios),
        "ref_forward": round(forward, 6),
        "ref_backward": round(backward, 6),
        "ref_margin": round(forward - backward, 6),
        "terms_forward": matched.get((a.id, b.id), []),
        "terms_backward": matched.get((b.id, a.id), []),
        "iol_prerequisite": round(ratios.get(a.id, 0.0), 6),
        "iol_dependent": round(ratios.get(b.id, 0.0), 6),
    }


ACCEPTED = "accepted"
PENDING = "pending"


def decide(votes):
    """``accepted``, ``pending`` or ``None`` for one ordered pair.

    Three binary votes produce exactly four scores, so "two thresholds" means
    choosing among three cut points rather than turning a dial. 3/3 is an edge,
    2/3 is a question for the teacher, anything less is discarded.

    **Position alone never creates an edge.** Temporal order votes on one
    direction of *every* pair -- with 24 concepts that is 276 votes cast before
    a word is read -- so at least one content criterion has to agree. Without
    that guard the middle band fills with pairs whose only evidence is that one
    paragraph came first, which is document order, not dependency.
    """
    content_votes = votes["semantic_reference"] + votes["inbound_outbound"]
    if not content_votes:
        return None

    score = votes["temporal_order"] + content_votes
    if score == 3:
        return ACCEPTED
    if score == 2:
        return PENDING
    return None


# A mention inside a contrastive clause says what something is *not* like.
#
# KNOWN LIMITATION, kept deliberately: this is a fixed list of English markers.
# It misses contrasts phrased without them ("solids do not flow", "different
# from a solid"), and it would not carry over to another language. A model that
# recognises contrast (NLI) would generalise; it was not adopted because it adds
# a model and slows the build, and whether contrast causes wrong edges in
# practice had not yet been measured.
# "than" added 2026-09-17: "more energy than in a solid" compares, it does not build on solids.
# Two further gaps: "than" also matches phrases that are not contrasts ("more
# than one flower"), so a genuine mention after it counts as contrastive; and
# ``vetoed`` checks mentions of the full name only -- a reference counted
# through the head word or through section containment is never vetoed.
_CONTRAST = re.compile(r"\b(while|whereas|unlike|but not|although|however|than)\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def only_contrastive_mentions(name, text):
    """True when every mention of ``name`` in ``text`` is contrastive.

    Contrast is scoped to the clause, not the sentence. "Liquids and gases can
    flow, while solids normally do not" contrasts *solids* only -- liquids and
    gases are what it is about.
    """
    records = []
    for sentence in _SENTENCE.split(text or ""):
        marker = _CONTRAST.search(sentence)
        if not marker:
            if mentions(normalize(sentence), name):
                records.append(False)
            continue
        before, after = sentence[:marker.start()], sentence[marker.end():]
        if mentions(normalize(before), name):
            records.append(False)
        if mentions(normalize(after), name):
            records.append(True)
    return bool(records) and all(records)


def vetoed(a, b, names):
    """A pair the votes allow but that must not become an edge.

    The criteria measure *how much* one concept looks like groundwork for
    another; they cannot recognise a pair that should never be an edge. Two
    checks remain, and both are about the pair itself rather than the lesson's
    wording elsewhere:

    * **Same concept.** Two concepts owning the same name cannot depend on
      each other.
    * **Only contrasted.** If ``b`` mentions ``a`` only to say what it is not
      like, reading ``b`` does not build on ``a``.

    Removed, and why:

    * *Siblings* keyed on the words of the topic's title. Renaming one object
      to "Diagram description for Solid" silently made it a sibling of Solid,
      Liquid and Gas and deleted 13 edges -- a rule whose output depends on
      title wording does not generalise.
    * *Mutual reference* matched names literally and fired on none of 139
      measured pairs.
    * The *author-statement override* of the contrast check was itself a fixed
      phrase list, and existed only to override the rules above.
    """
    a_name, b_name = names.get(a.id), names.get(b.id)
    if a_name and a_name == b_name:
        return True
    if a_name and only_contrastive_mentions(a_name, concept_text(b)):
        return True
    return False


def section_headings(concept):
    """The lesson headings a concept sits under, across every file teaching it.

    Taken from the documents' own structure (the heading each passage was
    extracted beneath), never from the words of a title, so renaming an object
    does not change it.
    """
    members = getattr(concept, "members", None) or (concept,)
    return {
        (member.section_title or "").strip().casefold()
        for member in members
        if (member.section_title or "").strip()
    }


def crosses_sections(a, b):
    """True when both concepts sit under headings and share none of them.

    Measured on a blind hand-check of 62 proposed edges: all 35 that crossed
    between sections ("Everyday examples of solids" -> "Gas") were judged wrong,
    because a lesson's Solids, Liquids and Gases sections are parallel topics.
    A concept with no heading -- a comparison at the end, the opening definition
    -- is not treated as crossing anything.
    """
    left, right = section_headings(a), section_headings(b)
    return bool(left and right and not (left & right))


def decide_pairs(concepts, runtime_instance=None):
    """Every ordered pair the criteria accept or send to the teacher, after vetoes.

    The cross-section flag is recorded for the teacher but no longer caps a
    verdict: see learning_path/CRITERIA.md (revision 2026-09-17).
    """
    # Examples and similar furniture present concepts; nothing depends on them
    # and they depend on nothing. They are ordered last by `order_with_links`.
    concepts = [concept for concept in concepts if not is_structural(concept)]
    if len(concepts) < 2:
        return []

    matrix, matched = reference_details(concepts, runtime_instance)
    ratios = inbound_outbound_ratios(concepts, matrix)
    names = concept_names(concepts)

    decisions = []
    for a in concepts:
        for b in concepts:
            if a.id == b.id:
                continue

            votes = cast_votes(a, b, matrix, ratios, matched)
            verdict = decide(votes)
            if verdict is None or vetoed(a, b, names):
                continue

            cross_section = crosses_sections(a, b)

            decisions.append({
                "prerequisite": a,
                "dependent": b,
                "verdict": verdict,
                "votes": votes,
                "cross_section": cross_section,
            })
    return decisions
