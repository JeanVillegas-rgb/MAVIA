"""The LLM checker: whether a concept's text supports a question's answer."""

import json
import logging

from django.conf import settings

from . import llm

logger = logging.getLogger(__name__)

PREVIEW_CHARS = 200


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
    """The draft Q&A as one string: what the checker reads, and the query when concepts are ranked."""
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


def _judge_call(prompt):
    """One checker call."""
    raw, _metrics = llm.generate(
        prompt,
        model=settings.QUESTION_JUDGE_MODEL,
        schema=JUDGE_SCHEMA,
        temperature=settings.QUESTION_JUDGE_TEMPERATURE,
        max_tokens=settings.QUESTION_JUDGE_MAX_TOKENS,
        timeout=settings.QUESTION_JUDGE_TIMEOUT,
    )
    return json.loads(raw)


def judge(question, passages):
    """(verdict, supported_answer, problem) for one draft and its passages."""
    prompt = JUDGE_PROMPT.format(
        passages="\n\n".join(
            f"[{index + 1}] ({chunk['concept']}) {chunk['text']}"
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
    return settings.QUESTION_VALIDATION_ENABLED


def verify(question, source):
    """One draft's verdict against the concept it was written from; a check that could not run comes back "unverified"."""
    read = {"concept": source["concept"], "preview": source["text"][:PREVIEW_CHARS]}
    if not source["text"].strip():
        return {
            "passed": True,
            "stage": "source_missing",
            "reason": "the concept has no text to check against",
            "verdict": "unverified",
            "source": read,
        }

    try:
        verdict, supported_answer, problem = judge(question, [source])
    except Exception as exc:
        logger.warning("Judge call failed for draft %s: %s", question.id, exc)
        return {
            "passed": True,
            "stage": "judge_unavailable",
            "reason": f"judge unavailable: {exc}",
            "verdict": "unverified",
            "source": read,
        }

    if verdict == "supported":
        return {
            "passed": True, "stage": "judge", "reason": "",
            "verdict": verdict, "source": read,
        }

    reason = problem or (
        f"the source does not support the answer {asserted_answer(question)!r}"
    )
    if verdict == "contradicted" and supported_answer:
        reason = f"{reason} -- the source supports {supported_answer!r}"
    return {
        "passed": False, "stage": "judge", "reason": reason,
        "verdict": verdict, "source": read,
    }


def correction_note(failures):
    """The feedback added to the next round's generation prompt: the rejected drafts and why."""
    if not failures:
        return ""
    lines = [
        f'- "{text}" was rejected: {reason}'
        for text, reason in failures[:settings.QUESTION_CORRECTION_LIMIT]
    ]
    return (
        "A previous attempt produced questions that were rejected. "
        "Do not repeat these mistakes:\n"
        + "\n".join(lines)
        + "\nAsk only about facts stated in the content above, and make sure "
        "the option you mark correct is the one the content states."
    )
