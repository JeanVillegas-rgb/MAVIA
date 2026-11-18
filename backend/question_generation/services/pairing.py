"""Finds the concept a teacher's question belongs to: rank the topic's concepts, then let the checker confirm."""

import logging

from .grounding import PREVIEW_CHARS, judge, question_query

logger = logging.getLogger(__name__)

PAIRED = "paired"
KEY_CONFLICT = "key_conflict"
UNMATCHED = "unmatched"
NO_ANSWER = "no_answer"
UNVERIFIED = "unverified"


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
