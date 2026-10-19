"""Jean's stored SBERT embeddings adapted to MAVIA's model contract."""

import hashlib
import os
from functools import lru_cache

from django.db import transaction

from lessons.models import LearningObject, OutlineNode, grouping_fingerprint


SBERT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class SemanticUnavailable(RuntimeError):
    """The shared sentence encoder cannot safely process the requested text."""


@lru_cache(maxsize=1)
def get_sbert_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(SBERT_MODEL)


def embed_texts(texts):
    if not texts:
        return []
    vectors = get_sbert_model().encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return vectors.tolist()


class SentenceEmbeddingRuntime:
    """Small shared adapter used by question pairing and grounding."""

    def __init__(self):
        try:
            self.encoder = get_sbert_model()
        except Exception as exc:
            raise SemanticUnavailable("The sentence encoder is unavailable.") from exc

    def supports(self, text):
        text = " ".join((text or "").split())
        if not text:
            return False
        try:
            token_count = len(
                self.encoder.tokenizer.encode(text, add_special_tokens=True)
            )
        except Exception as exc:
            raise SemanticUnavailable("The sentence encoder could not read the text.") from exc
        return token_count <= self.encoder.max_seq_length

    def embeddings(self, texts):
        texts = [" ".join((text or "").split()) for text in texts]
        if not all(self.supports(text) for text in texts):
            raise SemanticUnavailable(
                "Empty or over-length content cannot be embedded safely."
            )
        try:
            return embed_texts(texts)
        except Exception as exc:
            raise SemanticUnavailable("Sentence embedding failed.") from exc


@lru_cache(maxsize=1)
def sentence_embedding_runtime():
    return SentenceEmbeddingRuntime()


def semantic_mode():
    """Retain the existing environment switch for question pairing only."""
    value = os.getenv("SEMANTIC_GROUPING_MODE", "auto").lower()
    if value not in {"legacy", "review", "auto"}:
        raise SemanticUnavailable(
            "SEMANTIC_GROUPING_MODE must be legacy, review, or auto."
        )
    return value


def cache_subtopic_embeddings(course):
    subtopics = list(
        OutlineNode.objects.filter(course=course, depth=1).select_related("parent")
    )
    outdated = []
    for node in subtopics:
        text = f"{node.parent.title} > {node.title}"
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if (
            node.embedding
            and node.embedding_model == SBERT_MODEL
            and node.embedding_text_hash == text_hash
        ):
            continue
        outdated.append((node, text, text_hash))
    if not outdated:
        return 0

    vectors = embed_texts([text for _, text, _ in outdated])
    for (node, _, text_hash), vector in zip(outdated, vectors):
        node.embedding = vector
        node.embedding_model = SBERT_MODEL
        node.embedding_text_hash = text_hash
    OutlineNode.objects.bulk_update(
        [node for node, _, _ in outdated],
        ["embedding", "embedding_model", "embedding_text_hash"],
    )
    return len(outdated)


def embed_learning_objects(draft_objects):
    object_ids = []
    texts = []
    for draft in draft_objects:
        body = (draft.get("content") or draft.get("source_text") or "").strip()
        if not body:
            continue
        object_ids.append(draft["draft_id"])
        title = draft.get("title") or "Untitled learning object"
        section = draft.get("section_title") or ""
        heading = f"{section} > {title}" if section and section != title else title
        texts.append(f"{heading}. {body}")
    return dict(zip(object_ids, embed_texts(texts)))


@transaction.atomic
def save_learning_objects(material, draft_objects):
    embeddings = embed_learning_objects(draft_objects)
    learning_objects = []
    for order, draft in enumerate(draft_objects):
        draft_id = draft["draft_id"]
        object_type = draft.get("type") or "text"
        kind = (
            LearningObject.Kind.IMAGE
            if object_type in {"image", "table"}
            else LearningObject.Kind.TEXT
        )
        visual_parts = [
            part
            for part in draft.get("parts", [])
            if part.get("type") in {"image", "table"}
        ]
        content = draft.get("content") or ""
        source_text = draft.get("source_text") or content
        first_visual = visual_parts[0] if visual_parts else {}
        learning_objects.append(LearningObject(
            material=material,
            draft_id=draft_id,
            kind=kind,
            title=draft.get("title") or "Untitled learning object",
            section_title=draft.get("section_title") or "",
            content=content,
            image_url=(first_visual.get("image_url") or "")[:500],
            source_page=draft.get("source_page"),
            source_block_id=(draft.get("source_block_ids") or [None])[0],
            source_excerpt=source_text,
            parts=visual_parts,
            order=order,
            embedding=embeddings.get(draft_id, []),
            grouping_content_hash=grouping_fingerprint(
                draft.get("title") or "Untitled learning object",
                content,
            ),
        ))

    material.learning_objects.all().delete()
    rows = LearningObject.objects.bulk_create(learning_objects)
    material.draft_learning_objects = draft_objects
    material.object_embeddings = embeddings
    material.save(update_fields=["draft_learning_objects", "object_embeddings"])
    return rows
