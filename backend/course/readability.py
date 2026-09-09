"""Relative readability comparison for two texts teaching the same concept.

This is deliberately not a classifier. It orders two passages and reports how
confident that ordering is, so an unconvincing comparison can be handed to a
teacher instead of guessed at. Thresholds are operating values chosen to be
conservative; they are not calibrated probabilities.
"""

import re


# A comparison is only automatic when every margin clears and both signals
# point the same way. See the design doc for the measurements behind these.
FK_MARGIN = 1.5
WORD_RATIO = 1.25
WORD_DELTA = 10

_VOWELS = "aeiouy"
_SENTENCE_SPLIT = re.compile(r"[.!?]+")
_NON_ALPHA = re.compile(r"[^a-z]")


def syllable_count(word):
    """Count vowel groups, discounting a silent trailing 'e'.

    An approximation. It is used only to compare two passages against each
    other, so a consistent bias matters less than a correct absolute count.
    """
    cleaned = _NON_ALPHA.sub("", word.lower())
    if not cleaned:
        return 0
    count = 0
    previous_was_vowel = False
    for character in cleaned:
        is_vowel = character in _VOWELS
        if is_vowel and not previous_was_vowel:
            count += 1
        previous_was_vowel = is_vowel
    if cleaned.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def text_metrics(text):
    words = (text or "").split()
    sentences = [part for part in _SENTENCE_SPLIT.split(text or "") if part.strip()]
    word_count = len(words)
    sentence_count = len(sentences)
    safe_words = word_count or 1
    safe_sentences = sentence_count or 1
    syllables = sum(syllable_count(word) for word in words)
    fk = (
        0.39 * (safe_words / safe_sentences)
        + 11.8 * (syllables / safe_words)
        - 15.59
    )
    return {"words": word_count, "sentences": sentence_count, "fk": round(fk, 2)}


def flesch_kincaid_grade(text):
    """US grade level. Reported for evidence; comparison uses text_metrics."""
    return text_metrics(text)["fk"]


def compare(original_text, candidate_text):
    """Where does ``candidate_text`` sit relative to ``original_text``?

    Returns the proposed slot plus the margins behind it. ``confident`` is
    False whenever the two signals disagree on direction, regardless of how
    large either margin is: a passage can be longer and easier at the same
    time, and that is precisely when a guess is least defensible.
    """
    original = text_metrics(original_text)
    candidate = text_metrics(candidate_text)

    delta_fk = abs(candidate["fk"] - original["fk"])
    delta_words = abs(candidate["words"] - original["words"])
    smaller = min(candidate["words"], original["words"]) or 1
    ratio = max(candidate["words"], original["words"]) / smaller

    fk_says_elaborated = candidate["fk"] > original["fk"]
    words_say_elaborated = candidate["words"] > original["words"]
    agree = fk_says_elaborated == words_say_elaborated

    # Flesch-Kincaid leads the proposal; word count only breaks an FK tie.
    if candidate["fk"] == original["fk"]:
        slot = "ELABORATED" if words_say_elaborated else "SIMPLIFIED"
    else:
        slot = "ELABORATED" if fk_says_elaborated else "SIMPLIFIED"

    confident = (
        agree
        and delta_fk > FK_MARGIN
        and ratio >= WORD_RATIO
        and delta_words > WORD_DELTA
    )

    return {
        "slot": slot,
        "confident": confident,
        "agree": agree,
        "delta_fk": round(delta_fk, 2),
        "delta_words": delta_words,
        "ratio": round(ratio, 2),
    }
