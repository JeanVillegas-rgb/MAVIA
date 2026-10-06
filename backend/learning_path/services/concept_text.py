"""What the learning-path clues read from a concept: sentences, terms, vectors, PDFs.

Nothing here decides anything; it turns grouped learning objects into the
material the relatedness gate and the four clues compare (spec section 4).
"""

import re
from dataclasses import dataclass, field

import numpy as np
from nltk.stem import PorterStemmer

from .text_signals import strip_part_suffix

MIN_SENTENCE_WORDS = 4
MIN_TERM_LETTERS = 3
MAX_NAME_TERMS = 6

# General English only. Words shaped by our lessons ("example", "part", "one")
# are left out on purpose: a list fitted to the lessons we test on is the kind
# of rule v5 removes.
STOP_WORDS = frozenset("""
a about above after again against all also am an and any are as at be because been
before being below between both but by can cannot could did do does doing down
during each few for from further had has have having he her here hers herself him
himself his how i if in into is it its itself just me more most my myself no nor not
now of off on once only or other others our ours ourselves out over own same she
should so some such than that the their theirs them themselves then there these they
this those through to too under until up very was we were what when where which while
who whom why will with would you your yours yourself yourselves
""".split())

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[a-z]+")
_LEADING_NUMBER = re.compile(r"^\s*\d+(?:\.\d+)*\s*[.):-]*\s*")
_stemmer = PorterStemmer()


def split_sentences(text):
    """Sentences of at least four words; shorter fragments are leftover headings."""
    return [
        sentence.strip()
        for sentence in _SENTENCE_BREAK.split(text or "")
        if len(sentence.split()) >= MIN_SENTENCE_WORDS
    ]


def _kept_words(text):
    return [
        word for word in _WORD.findall((text or "").lower())
        if word not in STOP_WORDS and len(word) >= MIN_TERM_LETTERS
    ]


def terms(text):
    """Porter stems of the content words, repeats kept: "fertilized" -> "fertil"."""
    return [_stemmer.stem(word) for word in _kept_words(text)]


def strip_numbering(title):
    """Drop a heading's list number: "7. Everyday Examples" -> "Everyday Examples"."""
    return _LEADING_NUMBER.sub("", title or "").strip()


def name_terms(title):
    """The stems a concept can be named by, or ``()`` when its title is a sentence."""
    stems = tuple(terms(strip_numbering(strip_part_suffix(title or ""))))
    return stems if 0 < len(stems) <= MAX_NAME_TERMS else ()


@dataclass
class ConceptText:
    concept: object
    sentences: list
    pdfs: list
    sentence_terms: list
    passages: list
    name: tuple
    spelling: dict = field(default_factory=dict)
    vectors: object = None
    # ``(title, stems)`` for each learning object inside the concept whose title is
    # a name of its own: "Pollination" inside "How Flowering Plants Reproduce".
    part_names: tuple = ()
    materials: frozenset = frozenset()

    @property
    def id(self):
        return self.concept.id


def _members(concept):
    return getattr(concept, "members", None) or (concept,)


def part_names(concept, name):
    """The members' titles a concept can also be named by, other than ``name`` itself.

    The extractor keeps a term in its learning object's title and the explanation
    in its content, so "Pollination" is never written in Pollination's own text.
    """
    found, seen = [], {frozenset(name)}
    for member in _members(concept):
        title = getattr(member, "title", "") or ""
        stems = name_terms(title)
        if stems and frozenset(stems) not in seen:
            seen.add(frozenset(stems))
            found.append((strip_numbering(strip_part_suffix(title)), stems))
    return tuple(found)


def prepare(concepts, embed=None):
    """One ``ConceptText`` per concept; ``embed`` is called once for every sentence."""
    texts = []
    for concept in concepts:
        sentences, pdfs, passages, spelling = [], [], [], {}
        for member in _members(concept):
            content = getattr(member, "content", "") or ""
            passages.append(terms(content))
            for word in _kept_words(content):
                spelling.setdefault(_stemmer.stem(word), word)
            for sentence in split_sentences(content):
                sentences.append(sentence)
                pdfs.append(getattr(member, "material_id", None))
        name = name_terms(concept.title)
        texts.append(ConceptText(
            concept=concept,
            sentences=sentences,
            pdfs=pdfs,
            sentence_terms=[terms(sentence) for sentence in sentences],
            passages=passages,
            name=name,
            spelling=spelling,
            part_names=part_names(concept, name),
            materials=frozenset(
                member.material_id for member in _members(concept)
                if getattr(member, "material_id", None) is not None
            ),
        ))
    if embed is not None:
        vectors = np.asarray(embed([sentence for text in texts for sentence in text.sentences]))
        start = 0
        for text in texts:
            count = len(text.sentences)
            text.vectors = vectors[start:start + count] if count else np.zeros((0, vectors.shape[1] if vectors.ndim == 2 else 0))
            start += count
    return texts


def material_positions(concepts):
    """``{material id: {concept id: position}}`` -- each PDF's own order of the concepts it teaches."""
    first_seen = {}
    for concept in concepts:
        for member in getattr(concept, "members", None) or ():
            spot = (member.order, getattr(member, "id", 0) or 0)
            seen = first_seen.setdefault(member.material_id, {})
            if concept.id not in seen or spot < seen[concept.id]:
                seen[concept.id] = spot
    return {
        material_id: {concept_id: position for position, concept_id in enumerate(sorted(seen, key=seen.get))}
        for material_id, seen in first_seen.items()
    }
