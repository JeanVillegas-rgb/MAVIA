"""Measure how a candidate version relates to the Normal text, without Gemma.

A version label is right only when three things are true of the candidate:

* **the facts are kept** -- every Normal sentence has a plausible match
  somewhere in the candidate (meaning coverage); a strong average cannot
  hide one weakly matched sentence;
* **something is added**, or not -- candidate sentences that match nothing in
  Normal (novelty);
* **it is easier to read**, or not -- the Dale-Chall score, which counts words
  most students do not know. FKGL counts only word and sentence length, so
  "intermolecular forces lock particles into a lattice" looked easy to it.

Coverage and novelty use the MiniLM sentence encoder semantic grouping already
loads; Dale-Chall is a formula over a fixed word list. No LLM is called.

The cut-offs were measured on 21 labelled pairs (the Solid, Liquid and Gas
PDFs, the observed failures in BUGS.md and written cases): every real
elaboration had at least one new sentence and every paraphrase none. Coverage's
0.55 was originally calibrated for the *mean*, not each sentence; applying it
per sentence is deliberately conservative and may send good paraphrases to
teacher review. It needs calibration on additional labelled pairs.
"""

import re
from functools import lru_cache
from pathlib import Path

# A candidate sentence closer than this to some Normal sentence says the same
# thing; one further from all of them is new content.
NOVEL_SENTENCE_SIMILARITY = 0.6
# Minimum plausible match for each Normal sentence. A failed sentence asks for
# review rather than automatically assigning a PDF-supplied role.
MIN_SENTENCE_COVERAGE = 0.55
# A sentence needs a few words to carry a fact; shorter fragments ("Examples:")
# are ignored when splitting.
_MIN_SENTENCE_WORDS = 3

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+|(?<=:)\s+(?=[A-Z])")
_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")
_DALE_CHALL_SENTENCE = re.compile(r"[.!?]+")


def _words(text):
    """Lower-case words; a typographic apostrophe is an apostrophe.

    Measured: "don’t" (with ’) split into "don" and "t", two unfamiliar
    words that failed a generated Simplified version.
    """
    return _WORD.findall((text or "").replace("’", "'").replace("‘", "'").casefold())


class MeasurementUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _easy_words():
    path = Path(__file__).with_name("data") / "dale_chall_easy_words.txt"
    return frozenset(line.strip().casefold() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _word_forms(word):
    """The word and its likely base forms: "solids" -> solid, "melted" -> melt."""
    forms = {word}
    for suffix, replacement in (("ies", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"),
                                ("ing", ""), ("ing", "e"), ("'s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            forms.add(word[: -len(suffix)] + replacement)
    return forms


def _familiar(word):
    return any(form in _easy_words() for form in _word_forms(word))


def dale_chall(text):
    """The Dale-Chall readability score: higher means harder words and sentences.

    A word counts as familiar when it or its base form is on the list, so
    "solids" and "melted" are not scored as harder than "solid" and "melt".
    Counting the vocabulary both texts share as familiar was tried and
    measured worse: a clarification is easier *because* it explains a hard
    word, and treating that word as familiar erased the difference.
    """
    words = _words(text)
    if not words:
        return 0.0
    sentence_count = len([part for part in _DALE_CHALL_SENTENCE.split(text) if part.strip()]) or 1
    difficult_percent = 100 * sum(not _familiar(word) for word in words) / len(words)
    score = 0.1579 * difficult_percent + 0.0496 * (len(words) / sentence_count)
    if difficult_percent > 5:
        score += 3.6365
    return score


def sentences(text):
    parts = [part.strip() for part in _SENTENCE_BREAK.split(text or "")]
    kept = [part for part in parts if len(part.split()) >= _MIN_SENTENCE_WORDS]
    return kept or [" ".join((text or "").split())]


def _similarities(normal_sentences, candidate_sentences):
    from lessons.services.semantic_grouping import SemanticUnavailable, runtime

    try:
        encoder = getattr(runtime(), "encoder", None)
    except SemanticUnavailable as exc:
        raise MeasurementUnavailable(str(exc)) from exc
    if encoder is None:
        # A runtime without a sentence encoder (a scoring-only stand-in)
        # cannot measure; treated as unavailable rather than failing.
        raise MeasurementUnavailable("no sentence encoder in the semantic runtime")
    normal = encoder.encode(normal_sentences, normalize_embeddings=True, convert_to_numpy=True)
    candidate = encoder.encode(candidate_sentences, normalize_embeddings=True, convert_to_numpy=True)
    return normal @ candidate.T  # rows: Normal sentences, columns: candidate sentences


def outside_terms(source_text, version_text):
    """Unfamiliar words a generated version uses that its source never does.

    Measured on the Solid, Liquid and Gas topic: generated Elaborated versions
    brought in "intermolecular forces", "kinetic energy", "phase change" and
    "constituent particles" -- terms the lesson never teaches. A common word
    (on the Dale-Chall list) or any form of a source word is not counted.
    """
    source = {form for word in _words(source_text) for form in _word_forms(word)}
    terms = {
        word for word in _words(version_text)
        if not _familiar(word) and not (_word_forms(word) & source)
    }
    return sorted(terms)


def measure_versions(normal_text, candidate_text):
    """``{facts_kept, adds_content, easier, ...}`` for one candidate against Normal."""
    normal_sentences = sentences(normal_text)
    candidate_sentences = sentences(candidate_text)
    similarity = _similarities(normal_sentences, candidate_sentences)
    coverage = [float(value) for value in similarity.max(axis=1)]
    closeness = [float(value) for value in similarity.max(axis=0)]
    novel = sum(value < NOVEL_SENTENCE_SIMILARITY for value in closeness)
    mean_coverage = sum(coverage) / len(coverage)
    weak_coverage = sum(value < MIN_SENTENCE_COVERAGE for value in coverage)
    dale_chall_change = dale_chall(candidate_text) - dale_chall(normal_text)
    return {
        "facts_kept": weak_coverage == 0,
        "adds_content": novel >= 1,
        "easier": dale_chall_change < 0,
        "mean_coverage": round(mean_coverage, 2),
        "min_coverage": round(min(coverage), 2),
        "weakly_covered_sentences": weak_coverage,
        "novel_sentences": novel,
        "dale_chall_change": round(dale_chall_change, 2),
    }
