"""Stand-ins shared by the learning-path tests: concepts, members, a fake encoder."""

import hashlib
from types import SimpleNamespace

import numpy as np

from .services.concept_text import terms

FAKE_DIMENSIONS = 256


def member(content, material_id=1, order=0, title="", section_title=""):
    return SimpleNamespace(
        content=content, material_id=material_id, order=order, title=title, section_title=section_title,
    )


def concept(id, title, *members, **extra):
    """A concept stub; a plain string member becomes one passage in PDF 1."""
    members = tuple(item if not isinstance(item, str) else member(item) for item in members)
    fields = {
        "id": id,
        "title": title,
        "order": extra.pop("order", 0),
        "kind": "text",
        "section_title": members[0].section_title if members else "",
        "content": members[0].content if members else "",
        "member_text": "\n".join(item.content for item in members),
        "members": members,
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


def word_vectors(sentences):
    """A fake encoder: sentences sharing stemmed words get similar vectors."""
    rows = np.zeros((len(sentences), FAKE_DIMENSIONS), dtype="float32")
    for row, sentence in zip(rows, sentences):
        for term in terms(sentence):
            row[int(hashlib.md5(term.encode("utf-8")).hexdigest(), 16) % FAKE_DIMENSIONS] += 1.0
        norm = np.linalg.norm(row)
        if norm:
            row /= norm
    return rows
