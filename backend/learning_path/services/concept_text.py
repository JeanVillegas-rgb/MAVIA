"""What the clues read from a concept: sentences, terms, vectors and PDF positions."""

import re
from dataclasses import dataclass, field

import numpy as np
from django.conf import settings
from nltk.stem import PorterStemmer

from lessons.services.learning_object_builder import find_body_font_size, is_heading, parse_numbered_concept
from lessons.services.lesson_pipeline import _teaching_blocks

from .text_signals import strip_part_suffix

MIN_SENTENCE_WORDS = settings.LEARNING_PATH_MIN_SENTENCE_WORDS
MIN_TERM_LETTERS = settings.LEARNING_PATH_MIN_TERM_LETTERS
MAX_NAME_TERMS = settings.LEARNING_PATH_MAX_NAME_TERMS
HEADING_SIZE_TOLERANCE = 0.5

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
    """Sentences of at least four words."""
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
    """Porter stems of the content words, repeats kept."""
    return [_stemmer.stem(word) for word in _kept_words(text)]


def strip_numbering(title):
    """Drop a heading's list number: "7. Everyday Examples" -> "Everyday Examples"."""
    return _LEADING_NUMBER.sub("", title or "").strip()


def name_terms(title):
    """The stems a concept can be named by, or () when its title is a sentence."""
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
    part_names: tuple = ()
    materials: frozenset = frozenset()
    headings: frozenset = frozenset()
    sections: frozenset = frozenset()

    @property
    def id(self):
        return self.concept.id


def _members(concept):
    return getattr(concept, "members", None) or (concept,)


def part_names(concept, name):
    """Titles of the concept's members it can also be named by."""
    found, seen = [], {frozenset(name)}
    for member in _members(concept):
        title = getattr(member, "title", "") or ""
        stems = name_terms(title)
        if stems and frozenset(stems) not in seen:
            seen.add(frozenset(stems))
            found.append((strip_numbering(strip_part_suffix(title)), stems))
    return tuple(found)


def _printed_sections(blocks, object_block_ids):
    """{block id: the section the block is printed in}, smaller headings nesting under bigger ones."""
    teaching = _teaching_blocks(blocks)
    body_font_size = find_body_font_size(teaching)
    if body_font_size is None:
        return {}
    ordered = sorted(teaching, key=lambda block: (block["page"], block["bbox"][1], block["bbox"][0]))
    starts = [index for index, block in enumerate(ordered) if str(block["block_id"]) in object_block_ids]
    current, current_size, skipping, sections = "", 0, False, {}
    for block in ordered[min(starts):] if starts else []:
        if block.get("starts_excluded_section"):
            current, current_size, skipping = "", 0, True
            continue
        if block.get("category") == "document_metadata":
            continue
        numbered = parse_numbered_concept(block["text"], block.get("leading_bold_text"))
        heading = is_heading(block, body_font_size)
        if skipping and not (heading or (numbered and block.get("leading_bold_text"))):
            continue
        if heading or numbered:
            skipping = False
            size = block.get("dominant_font_size") or 0
            if heading and (not current or size >= current_size - HEADING_SIZE_TOLERANCE):
                current, current_size = (numbered[0] if numbered else block["text"]), size
        sections[str(block["block_id"])] = current
    return sections


def enclosing_headings(concepts):
    """{object id: heading} for a text object printed inside a section other than its own."""
    by_material = {}
    for concept in concepts:
        for member in getattr(concept, "members", None) or ():
            if getattr(member, "material", None) is not None:
                by_material.setdefault(member.material_id, {})[member.id] = member
    found = {}
    for members in by_material.values():
        items = list(members.values())
        sections = _printed_sections(
            items[0].material.extracted_blocks or [],
            {str(item.source_block_id) for item in items if item.source_block_id is not None},
        )
        for item in items:
            printed_in = sections.get(str(item.source_block_id), "")
            if item.kind != "image" and printed_in and printed_in != (item.section_title or ""):
                found[item.id] = printed_in
    return found


def _member_headings(concept, enclosing):
    """Every heading the concept's members sit under, including the one a term is printed under."""
    headings = []
    for member in getattr(concept, "members", None) or ():
        headings.append(getattr(member, "section_title", "") or "")
        headings.append(enclosing.get(getattr(member, "id", None), ""))
    return headings


def prepare(concepts, embed=None):
    """One ConceptText per concept; embed is called once for every sentence."""
    concepts = list(concepts)
    enclosing = enclosing_headings(concepts)
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
        headings = _member_headings(concept, enclosing)
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
            headings=frozenset(frozenset(terms(heading)) for heading in headings) - {frozenset()},
            sections=frozenset(heading.strip().casefold() for heading in headings if heading.strip()),
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
    """{material id: {concept id: position}}: each PDF's own order."""
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
