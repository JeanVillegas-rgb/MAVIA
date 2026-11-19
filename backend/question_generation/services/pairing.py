"""Finds the concept a question belongs to: index the topic's concepts, rank them against the question, then let the checker confirm."""

import logging
import re

from .concept_source import concept_name, concept_source_text, is_concept_source
from .grounding import PREVIEW_CHARS, judge, question_query

logger = logging.getLogger(__name__)

PAIRED = "paired"
KEY_CONFLICT = "key_conflict"
UNMATCHED = "unmatched"
NO_ANSWER = "no_answer"
UNVERIFIED = "unverified"


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _passages(engine, text):
    """The text as one passage, or cut at sentence ends when the encoder cannot read it whole."""
    text = " ".join((text or "").split())
    if not text:
        return []
    if engine.supports(text):
        return [text]
    passages, current = [], ""
    for sentence in _SENTENCE_END.split(text):
        joined = f"{current} {sentence}".strip()
        if engine.supports(joined):
            current = joined
            continue
        if current:
            passages.append(current)
        current = _fit(engine, sentence)
    if current:
        passages.append(current)
    return passages


def _fit(engine, text):
    """Trim text until the encoder accepts it, or "" if it never does."""
    words = (text or "").split()
    while words:
        candidate = " ".join(words)
        if engine.supports(candidate):
            return candidate
        words = words[:int(len(words) * 0.9)] if len(words) > 10 else words[:-1]
    return ""


class TopicIndex:
    """One topic's concepts as passages with their embeddings; built once per run."""

    def __init__(self, chunks, vectors=None, owners=None, reason=""):
        self.chunks = chunks
        self.vectors = vectors
        self.owners = owners or []
        self.reason = reason

    @property
    def searchable(self):
        return bool(self.vectors) and bool(self.chunks)

    def rank(self, query):
        """Every concept by cosine to the query, highest first; empty when unsearchable."""
        if not self.searchable:
            return []
        from lessons.services.embedding_service import sentence_embedding_runtime

        engine = sentence_embedding_runtime()
        fitted = _fit(engine, query)
        if not fitted:
            return []
        vector = engine.embeddings([fitted])[0]
        best = {}
        for candidate, owner in zip(self.vectors, self.owners):
            score = sum(a * b for a, b in zip(vector, candidate))
            if owner not in best or score > best[owner]:
                best[owner] = score
        scored = [(score, self.chunks[owner]) for owner, score in best.items()]
        scored.sort(key=lambda row: -row[0])
        return scored


def build_index(outline_node):
    """Index every concept of one topic's completed PDFs by the text its questions are written from. Never raises."""
    from lessons.models import LearningMaterial, LearningObject

    objects = LearningObject.objects.filter(
        material__outline_node=outline_node,
        material__status=LearningMaterial.Status.COMPLETED,
    ).exclude(content="").select_related("material", "group").order_by(
        "material__created_at", "material_id", "order", "id",
    )

    chunks = [
        {
            "concept": concept_name(item),
            "object_id": item.id,
            "material_id": item.material_id,
            "material_title": item.material.title,
            "text": text,
        }
        for item in objects
        if is_concept_source(item) and (text := concept_source_text(item).strip())
    ]
    if not chunks:
        return TopicIndex([], reason="no concept text to index")

    try:
        from lessons.services.embedding_service import sentence_embedding_runtime

        engine = sentence_embedding_runtime()
    except Exception as exc:
        logger.warning("Grounding index has no sentence encoder: %s", exc)
        return TopicIndex(chunks, reason=f"sentence encoder unavailable: {exc}")

    pieces = [
        (owner, passage)
        for owner, chunk in enumerate(chunks) for passage in _passages(engine, chunk["text"])
    ]
    if not pieces:
        return TopicIndex(chunks, reason="no passage fit the encoder window")

    try:
        vectors = engine.embeddings([passage for _, passage in pieces])
    except Exception as exc:
        logger.warning("Grounding index could not embed passages: %s", exc)
        return TopicIndex(chunks, reason=f"embedding failed: {exc}")

    return TopicIndex(chunks, vectors=vectors, owners=[owner for owner, _ in pieces])


def _candidates(question, index):
    """The topic's concepts in the order to try them: most similar to the question first."""
    ranked = index.rank(question_query(question))
    if ranked:
        return ranked
    return [(None, chunk) for chunk in index.chunks]


def pair_question(question, index):
    """The first concept, in rank order, whose text supports the question's answer key."""
    if not str(question.correct_answer or "").strip():
        return {"status": NO_ANSWER, "concept": None, "supported_answer": "", "tried": []}

    tried, conflict = [], None
    for score, chunk in _candidates(question, index):
        try:
            verdict, supported_answer, problem = judge(question, [chunk])
        except Exception as exc:
            logger.warning("Checker call failed while pairing a question: %s", exc)
            return {"status": UNVERIFIED, "concept": None, "supported_answer": "", "tried": tried}
        tried.append({
            "concept": chunk["concept"],
            "object_id": chunk["object_id"],
            "score": None if score is None else round(score, 4),
            "verdict": verdict,
            "reason": problem,
            "preview": chunk["text"][:PREVIEW_CHARS],
        })
        if verdict == "supported":
            return {"status": PAIRED, "concept": chunk, "supported_answer": "", "tried": tried}
        if verdict == "contradicted" and conflict is None:
            conflict = (chunk, supported_answer)

    if conflict is not None:
        return {"status": KEY_CONFLICT, "concept": conflict[0], "supported_answer": conflict[1], "tried": tried}
    return {"status": UNMATCHED, "concept": None, "supported_answer": "", "tried": tried}
