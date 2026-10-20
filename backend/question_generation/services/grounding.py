"""Checks each draft question against the topic's PDFs: retrieve the top passages, then ask an LLM checker."""

import json
import logging

import requests
from django.conf import settings
from config.groq_client import generate as groq_generate

logger = logging.getLogger(__name__)

CHUNK_WORDS = 90
CHUNK_OVERLAP = 30

PREVIEW_CHARS = 200


def _chunk_text(text):
    """Overlapping word windows over one PDF's extracted text."""
    words = (text or "").split()
    if not words:
        return []
    if len(words) <= CHUNK_WORDS:
        return [" ".join(words)]
    step = max(1, CHUNK_WORDS - CHUNK_OVERLAP)
    chunks = []
    for start in range(0, len(words), step):
        window = words[start:start + CHUNK_WORDS]
        if not window:
            break
        chunks.append(" ".join(window))
        if start + CHUNK_WORDS >= len(words):
            break
    return chunks


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
    """Chunked PDF text of one topic with its embeddings; built once per run."""

    def __init__(self, chunks, vectors=None, reason=""):
        self.chunks = chunks
        self.vectors = vectors
        self.reason = reason

    @property
    def searchable(self):
        return bool(self.vectors) and bool(self.chunks)

    def search(self, query, k=None):
        """Top-k chunks by cosine, highest first; empty when unsearchable."""
        k = k or int(getattr(settings, "QUESTION_VALIDATION_TOP_K", 3))
        if not self.searchable:
            return []
        from lessons.services.embedding_service import sentence_embedding_runtime

        engine = sentence_embedding_runtime()
        fitted = _fit(engine, query)
        if not fitted:
            return []
        vector = engine.embeddings([fitted])[0]
        scored = [
            (sum(a * b for a, b in zip(vector, candidate)), chunk)
            for candidate, chunk in zip(self.vectors, self.chunks)
        ]
        scored.sort(key=lambda row: -row[0])
        return scored[:k]


def build_index(outline_node):
    """Index every completed PDF of one topic. Never raises."""
    from lessons.models import LearningMaterial

    materials = LearningMaterial.objects.filter(
        outline_node=outline_node, status=LearningMaterial.Status.COMPLETED,
    ).order_by("created_at", "id")

    chunks = []
    for material in materials:
        for body in _chunk_text(material.extracted_text or ""):
            chunks.append({
                "material_id": material.id,
                "material_title": material.title,
                "text": body,
            })

    if not chunks:
        return TopicIndex([], reason="no extracted PDF text to index")

    try:
        from lessons.services.embedding_service import sentence_embedding_runtime

        engine = sentence_embedding_runtime()
    except Exception as exc:
        logger.warning("Grounding index has no sentence encoder: %s", exc)
        return TopicIndex(chunks, reason=f"sentence encoder unavailable: {exc}")

    usable, texts = [], []
    for chunk in chunks:
        fitted = _fit(engine, chunk["text"])
        if fitted:
            usable.append(chunk)
            texts.append(fitted)
    if not texts:
        return TopicIndex(chunks, reason="no chunk fit the encoder window")

    try:
        vectors = engine.embeddings(texts)
    except Exception as exc:
        logger.warning("Grounding index could not embed chunks: %s", exc)
        return TopicIndex(chunks, reason=f"embedding failed: {exc}")

    return TopicIndex(usable, vectors=vectors)


def asserted_answer(question):
    """What the draft claims is true, written out rather than as a letter."""
    if question.question_format == "TF":
        return str(question.correct_answer)
    choices = question.choices or {}
    key = str(question.correct_answer).strip()
    if isinstance(choices, dict) and key in choices:
        return f"{key}) {choices[key]}"
    return key


def question_query(question):
    """The draft Q&A as one string, used as the search query and checked text."""
    parts = [question.question_text]
    choices = question.choices or {}
    if isinstance(choices, dict):
        parts.extend(f"{letter}) {text}" for letter, text in sorted(choices.items()))
    elif isinstance(choices, (list, tuple)):
        parts.extend(str(text) for text in choices)
    parts.append(f"Answer: {asserted_answer(question)}")
    return "\n".join(str(part) for part in parts if part)


JUDGE_PROMPT = """You are a strict fact-checker for a science lesson for young
learners. You are given SOURCE passages taken from the lesson's own materials,
and one draft quiz question with the answer it claims is correct.

Decide ONLY from SOURCE. Do not use outside knowledge. If SOURCE does not say
it, it is not supported -- even if you know it to be true in general.

Return one JSON object:
- verdict: "supported" if SOURCE states or directly implies that the claimed
  answer is correct; "contradicted" if SOURCE indicates a different answer is
  correct; "unsupported" if SOURCE simply does not settle it.
- supported_answer: when the verdict is "contradicted", the option SOURCE
  actually supports, copied exactly from the draft's options. Otherwise "".
- problem: one short sentence naming the specific unsupported or contradicted
  claim. Empty string when the verdict is "supported".

SOURCE:
{passages}

DRAFT QUESTION:
{draft}
"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["supported", "contradicted", "unsupported"],
        },
        "supported_answer": {"type": "string"},
        "problem": {"type": "string"},
    },
    "required": ["verdict", "supported_answer", "problem"],
}


def _judge_model():
    return getattr(settings, "QUESTION_JUDGE_MODEL", settings.QUESTION_LLM_MODEL)


def _judge_call(prompt):
    """One checker call, at temperature 0."""
    if settings.LLM_PROVIDER == "groq":
        raw, _metrics = groq_generate(
            prompt,
            model=_judge_model(),
            schema=JUDGE_SCHEMA,
            temperature=0.0,
            max_tokens=512,
            timeout=getattr(settings, "QUESTION_JUDGE_TIMEOUT", settings.OLLAMA_TIMEOUT),
        )
        return json.loads(raw)
    response = requests.post(
        f"{settings.OLLAMA_BASE_URL}/api/generate",
        json={
            "model": _judge_model(),
            "prompt": prompt,
            "stream": False,
            "format": JUDGE_SCHEMA,
            "keep_alive": getattr(
                settings, "QUESTION_LLM_KEEP_ALIVE", settings.OLLAMA_KEEP_ALIVE
            ),
            "options": {"temperature": 0.0, "num_predict": 512},
        },
        timeout=getattr(settings, "QUESTION_JUDGE_TIMEOUT", settings.OLLAMA_TIMEOUT),
    )
    response.raise_for_status()
    body = response.json()
    if body.get("error"):
        raise RuntimeError(str(body["error"]))
    return json.loads(body.get("response") or "{}")


def judge(question, passages):
    """(verdict, supported_answer, problem) for one draft and its passages."""
    prompt = JUDGE_PROMPT.format(
        passages="\n\n".join(
            f"[{index + 1}] ({chunk['material_title']}) {chunk['text']}"
            for index, chunk in enumerate(passages)
        ),
        draft=question_query(question),
    )
    result = _judge_call(prompt)
    verdict = str(result.get("verdict") or "").strip().lower()
    if verdict not in ("supported", "contradicted", "unsupported"):
        verdict = "unsupported"
    return (
        verdict,
        str(result.get("supported_answer") or "").strip(),
        str(result.get("problem") or "").strip(),
    )


def enabled():
    return bool(getattr(settings, "QUESTION_VALIDATION_ENABLED", True))


def verify(question, index):
    """One draft's verdict; a check that could not run passes as unverified."""
    if not index.searchable:
        return {
            "passed": True,
            "stage": "retrieval_unavailable",
            "reason": index.reason or "retrieval unavailable",
            "verdict": "unverified",
            "retrieved": [],
        }

    hits = index.search(question_query(question))
    if not hits:
        return {
            "passed": False,
            "stage": "retrieval",
            "reason": "no source passage matched this question",
            "verdict": "unsupported",
            "retrieved": [],
        }

    passages = [chunk for _score, chunk in hits]
    retrieved = [
        {
            "material_id": chunk["material_id"],
            "score": round(score, 4),
            "preview": chunk["text"][:PREVIEW_CHARS],
        }
        for score, chunk in hits
    ]

    try:
        verdict, supported_answer, problem = judge(question, passages)
    except Exception as exc:
        logger.warning("Judge call failed for draft %s: %s", question.id, exc)
        return {
            "passed": True,
            "stage": "judge_unavailable",
            "reason": f"judge unavailable: {exc}",
            "verdict": "unverified",
            "retrieved": retrieved,
        }

    if verdict == "supported":
        return {
            "passed": True, "stage": "judge", "reason": "",
            "verdict": verdict, "retrieved": retrieved,
        }

    reason = problem or (
        f"the source does not support the answer {asserted_answer(question)!r}"
    )
    if verdict == "contradicted" and supported_answer:
        reason = f"{reason} -- the source supports {supported_answer!r}"
    return {
        "passed": False, "stage": "judge", "reason": reason,
        "verdict": verdict, "retrieved": retrieved,
    }


def correction_note(failures):
    """The feedback added to the generation prompt on a corrective retry."""
    if not failures:
        return ""
    lines = [f'- "{text}" was rejected: {reason}' for text, reason in failures[:6]]
    return (
        "A previous attempt produced questions that were rejected. "
        "Do not repeat these mistakes:\n"
        + "\n".join(lines)
        + "\nAsk only about facts stated in the content above, and make sure "
        "the option you mark correct is the one the content states."
    )
