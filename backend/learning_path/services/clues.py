"""The direction clues (spec section 6 and amendment 1).

Each looks at one pair and votes +1 (prerequisite first), -1 (dependent first)
or 0, with the numbers behind the vote. A clue votes whenever its evidence
differs at all; how far to trust it is fusion's job, so no clue has a threshold
that could be fitted to a lesson.
"""

import math
from collections import Counter

import numpy as np

from .concept_text import terms
from .relatedness import UNRELATED_PERCENTILE, relatedness

# Chi-squared with one degree of freedom at p < 0.05 (Dunning 1993).
SIGNIFICANT_G2 = 3.84
# How many best matches the meaning clue averages; 2 is the size fallback the
# spec defines, switched on only if the development set shows a size bias.
MEANING_MATCHES = 1
# v6.1: a chunk uses another concept's terms only when it has two of them (spec 2026-10-02, C4).
MIN_SHARED_TERMS = 2


def _sign(difference):
    return (difference > 0) - (difference < 0)


def name_use(holder, target):
    """Share of the holder's sentences containing every stem of the target's name."""
    if not target.name or not holder.sentence_terms:
        return 0.0
    needed = set(target.name)
    return sum(1 for stems in holder.sentence_terms if needed <= set(stems)) / len(holder.sentence_terms)


def name_vote(prerequisite, dependent):
    if prerequisite.name and set(prerequisite.name) == set(dependent.name):
        # One title on two concepts (a split the grouping made) names neither over the other.
        return 0, {"use": 0.0, "use_back": 0.0}
    use, use_back = name_use(dependent, prerequisite), name_use(prerequisite, dependent)
    return _sign(use - use_back), {"use": round(use, 3), "use_back": round(use_back, 3)}


def log_likelihood(count_inside, total_inside, count_outside, total_outside):
    """Dunning's G2 for "this term is characteristic of the inside text"."""
    total = total_inside + total_outside
    used = count_inside + count_outside
    observed = (count_inside, total_inside - count_inside, count_outside, total_outside - count_outside)
    expected = (
        total_inside * used / total, total_inside * (total - used) / total,
        total_outside * used / total, total_outside * (total - used) / total,
    )
    return 2 * sum(seen * math.log(seen / guess) for seen, guess in zip(observed, expected) if seen > 0)


def find_term_owners(texts):
    """``{stem: concept id}`` for terms significantly over-represented in one concept.

    Only terms at least two concepts use can link anything, so only those are owned.
    """
    counts = {text.id: Counter(term for passage in text.passages for term in passage) for text in texts}
    sizes = {concept_id: sum(counter.values()) for concept_id, counter in counts.items()}
    grand_total = sum(sizes.values())
    owners = {}
    for term in {term for counter in counts.values() for term in counter}:
        users = [concept_id for concept_id in counts if counts[concept_id][term]]
        if len(users) < 2:
            continue
        total_use = sum(counts[concept_id][term] for concept_id in users)
        best_score, best_owner = SIGNIFICANT_G2, None
        for concept_id in users:
            inside = counts[concept_id][term]
            outside_size = grand_total - sizes[concept_id]
            if not outside_size or inside / sizes[concept_id] <= (total_use - inside) / outside_size:
                continue
            score = log_likelihood(inside, sizes[concept_id], total_use - inside, outside_size)
            if score >= best_score:
                best_score, best_owner = score, concept_id
        if best_owner is not None:
            owners[term] = best_owner
    return owners


def term_use(holder, target, term_owners, min_terms=1):
    """Share of the holder's passages using at least ``min_terms`` distinct terms the target owns."""
    passages = [passage for passage in holder.passages if passage]
    if not passages:
        return 0.0
    return sum(
        1 for passage in passages
        if len({term for term in passage if term_owners.get(term) == target.id}) >= min_terms
    ) / len(passages)


def owned_words(holder, owner, term_owners):
    return sorted({
        holder.spelling.get(term, term)
        for passage in holder.passages for term in passage
        if term_owners.get(term) == owner.id
    })


def term_vote(prerequisite, dependent, term_owners, min_terms=1):
    use = term_use(dependent, prerequisite, term_owners, min_terms)
    use_back = term_use(prerequisite, dependent, term_owners, min_terms)
    record = {"owned": owned_words(dependent, prerequisite, term_owners),
              "use": round(use, 3), "use_back": round(use_back, 3)}
    if min_terms > 1:
        record.update(
            owned_back=owned_words(prerequisite, dependent, term_owners),
            single_word_use=round(term_use(dependent, prerequisite, term_owners), 3),
            single_word_use_back=round(term_use(prerequisite, dependent, term_owners), 3),
        )
    return _sign(use - use_back), record


def meaning_use(holder, target, meaning_cutoff):
    """Share of the holder's sentences whose closest match in the target clears the cutoff."""
    if holder.vectors is None or target.vectors is None or not len(holder.vectors) or not len(target.vectors):
        return 0.0
    similarity = holder.vectors @ target.vectors.T
    matches = min(MEANING_MATCHES, similarity.shape[1])
    closest = np.sort(similarity, axis=1)[:, -matches:].mean(axis=1)
    return float((closest > meaning_cutoff).mean())


def meaning_vote(prerequisite, dependent, meaning_cutoff):
    use = meaning_use(dependent, prerequisite, meaning_cutoff)
    use_back = meaning_use(prerequisite, dependent, meaning_cutoff)
    return _sign(use - use_back), {"use": round(use, 3), "use_back": round(use_back, 3)}


def meaning_cutoff(unrelated_pairs):
    """The 95th percentile of a sentence's closest match in an unrelated concept."""
    matches = []
    for first, second in unrelated_pairs:
        if first.vectors is None or second.vectors is None or not len(first.vectors) or not len(second.vectors):
            continue
        similarity = first.vectors @ second.vectors.T
        matches.extend(similarity.max(axis=1).tolist())
        matches.extend(similarity.max(axis=0).tolist())
    return float(np.percentile(matches, UNRELATED_PERCENTILE)) if matches else None


def order_vote(prerequisite, dependent, positions):
    """PDF order, only when two or more PDFs teach both and all agree."""
    shared = [spots for spots in positions.values() if prerequisite.id in spots and dependent.id in spots]
    first_count = sum(1 for spots in shared if spots[prerequisite.id] < spots[dependent.id])
    record = {"pdfs": len(shared), "agree": max(first_count, len(shared) - first_count)}
    if len(shared) < 2:
        return 0, record
    if first_count == len(shared):
        return 1, record
    if first_count == 0:
        return -1, record
    return 0, record



def shared_pdf_order(first, second, positions):
    """``(direction, pdfs)`` from every PDF teaching both concepts.

    +1 when all of them put ``first`` earlier, -1 when all put ``second``
    earlier, 0 when they disagree, ``None`` when no PDF teaches both.
    Unlike ``order_vote``, one PDF is enough: v6 takes direction from the
    lesson's own order (spec section 3).
    """
    shared = [spots for spots in positions.values() if first.id in spots and second.id in spots]
    if not shared:
        return None, 0
    first_count = sum(1 for spots in shared if spots[first.id] < spots[second.id])
    if first_count == len(shared):
        return 1, len(shared)
    if first_count == 0:
        return -1, len(shared)
    return 0, len(shared)


def reference_uses(earlier, later, term_owners, min_terms=1):
    """How much each concept refers to the other, by name and by owned terms.

    ``*_word`` are the single-word shares v6.1 keeps to tell a weak link from none.
    """
    same_name = bool(earlier.name) and set(earlier.name) == set(later.name)
    return {
        # One title on two concepts (a split the grouping made) names neither.
        "later_names_earlier": 0.0 if same_name else name_use(later, earlier),
        "earlier_names_later": 0.0 if same_name else name_use(earlier, later),
        "later_uses_earlier_terms": term_use(later, earlier, term_owners, min_terms),
        "earlier_uses_later_terms": term_use(earlier, later, term_owners, min_terms),
        "later_uses_earlier_word": term_use(later, earlier, term_owners),
        "earlier_uses_later_word": term_use(earlier, later, term_owners),
    }

def heading_stems(text):
    """The stems of every heading the concept's members sit under."""
    members = getattr(text.concept, "members", None) or ()
    return {frozenset(terms(getattr(member, "section_title", "") or "")) for member in members} - {frozenset()}


def _named_by(text, heading):
    return bool(text.name) and set(text.name) <= heading


def heading_vote(prerequisite, dependent):
    """Author structure: the dependent sits under a heading naming the prerequisite (Wang et al. 2016)."""
    under = any(_named_by(prerequisite, heading) for heading in heading_stems(dependent))
    under_back = any(_named_by(dependent, heading) for heading in heading_stems(prerequisite))
    return _sign(under - under_back), {"under": under, "under_back": under_back}


def presented_in_parallel(first, second):
    """Both sit under one heading that names neither: the author presents them side by side."""
    return any(
        not _named_by(first, heading) and not _named_by(second, heading)
        for heading in heading_stems(first) & heading_stems(second)
    )


def pair_votes(texts, term_owners, positions, related_cutoff, meaning_cutoff, semantic=True):
    """Relatedness and the four votes for every pair, each read as "first before second"."""
    for index, first in enumerate(texts):
        for second in texts[index + 1:]:
            score = relatedness(first, second) if semantic else None
            yield {
                "first": first,
                "second": second,
                "relatedness": score,
                # A concept with no full sentence has nothing to compare, with or without the encoder.
                "related": bool(first.sentences and second.sentences) and ((not semantic) or score >= related_cutoff),
                "votes": {
                    "name": name_vote(first, second)[0],
                    "terms": term_vote(first, second, term_owners)[0],
                    "meaning": meaning_vote(first, second, meaning_cutoff)[0] if semantic else 0,
                    "heading": heading_vote(first, second)[0],
                    "order": order_vote(first, second, positions)[0],
                    "parallel": presented_in_parallel(first, second),
                },
            }


def clue_records(prerequisite, dependent, term_owners, positions, meaning_cutoff, semantic=True, min_terms=1):
    """The numbers behind each clue for "prerequisite before dependent", for the stored evidence."""
    records = {
        "name": name_vote(prerequisite, dependent)[1],
        "terms": term_vote(prerequisite, dependent, term_owners, min_terms)[1],
        "heading": heading_vote(prerequisite, dependent)[1],
        "order": order_vote(prerequisite, dependent, positions)[1],
    }
    if semantic:
        records["meaning"] = meaning_vote(prerequisite, dependent, meaning_cutoff)[1]
    return records
