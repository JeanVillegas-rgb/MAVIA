import json
import logging
import re
import threading
import time

import requests
from django.conf import settings
from config.groq_client import GroqRateLimitError, generate as groq_generate

logger = logging.getLogger(__name__)

QUESTION_TEMPERATURE = settings.QUESTION_TEMPERATURE
QUESTION_NUM_PREDICT = settings.QUESTION_NUM_PREDICT
MAX_UNUSABLE_REPLIES = settings.QUESTION_MAX_UNUSABLE_REPLIES
_warm_lock = threading.Lock()
_warm_model = ""
_warm_until = 0.0


def _mark_question_model_warm():
    global _warm_model, _warm_until
    with _warm_lock:
        _warm_model = settings.QUESTION_LLM_MODEL
        _warm_until = time.monotonic() + 25 * 60


_GROUNDING_RULE = (
    "Use ONLY the facts stated in the content below. Do not add facts, "
    "terms, examples, numbers, causes or categories the content does not "
    "state, even if you know them to be true. If the content does not "
    "settle something, do not ask about it.\n"
    "The correct answer must be stated in the content or follow directly "
    "from it. Every wrong choice must be wrong because the content says "
    "otherwise.\n"
)

THINKING_LEVELS = {
    "remember": "Remember: recognize or recall a fact, term or definition the content states (identify, name, define, list).",
    "understand": (
        "Understand: show the meaning of what the content states by explaining it, giving or "
        "recognizing an example, classifying, summarizing, inferring, or comparing two things it describes."
    ),
    "apply": "Apply: use a rule or procedure the content states in a given situation (carry out, use).",
    "analyze": (
        "Analyze: break what the content describes into its parts and work out how the parts "
        "relate to one another or to the whole (differentiate, organize, find the cause)."
    ),
    "evaluate": (
        "Evaluate: judge, against a standard the content states, which option fits or which "
        "claim is correct (check, critique)."
    ),
}

TIER_LEVELS = {
    "LOT": ("remember", "understand", "apply"),
    "HOT": ("analyze", "evaluate"),
}

ITEM_RULES = (
    "- Test one idea per question, and state that idea in the question itself.\n"
    "- Use the simple words of the content; leave out wording the question does not need.\n"
    "- Word each question positively; avoid NOT and EXCEPT."
)

PROMPT_TEMPLATE = (
    "You are a quiz maker. Given the content below, write {count} questions.\n"
    + _GROUNDING_RULE +
    "Write each question at one of these thinking levels, spread across them:\n{levels}\n\n"
    "Item-writing rules:\n{item_rules}\n\n"
    "{format_request}\n\n"
    "Content:\n{content}\n\n"
    "{format_instructions}\n\n"
    "Respond ONLY with valid JSON, no other text. Every question object must\n"
    "carry a \"format\" field saying which kind it is. Use this exact structure:\n"
    '{{"questions": [{question_schema}]}}'
)

FORMAT_INSTRUCTIONS = {
    "MCQ": (
        "Each multiple-choice question has exactly 4 choices (A, B, C, D) and only one "
        "correct answer. Make every wrong choice plausible: a mistake a learner could make "
        "about this content. Keep the choices alike in length and grammar, and do not let "
        "them overlap. Do not use 'all of the above' or 'none of the above'. Do not give the "
        "answer away with words such as 'always' or 'never', or by repeating words from the question."
    ),
    "TF": (
        "Each true/false item is one statement that the content makes clearly true or clearly "
        "false. Test one idea per statement, avoid negatives, and avoid words such as 'always', "
        "'never' or 'all' that give the answer away. Include a brief explanation for the correct answer."
    ),
}

QUESTION_SCHEMA = {
    "MCQ": (
        '{"question": "...", "format": "MCQ", '
        '"choices": {"A": "...", "B": "...", "C": "...", "D": "..."}, '
        '"correct_answer": "A or B or C or D", "explanation": "brief explanation"}'
    ),
    "TF": (
        '{"question": "statement here", "format": "TF", '
        '"correct_answer": "True or False", "explanation": "brief explanation"}'
    ),
}

SUPPORTED_FORMATS = ("MCQ", "TF")


def _ollama_metrics(data):
    """Ollama's nanosecond timings in trace-friendly units."""
    def milliseconds(name):
        value = data.get(name)
        return round(value / 1_000_000, 1) if isinstance(value, (int, float)) else None

    eval_count = data.get("eval_count") or 0
    eval_duration = data.get("eval_duration") or 0
    tokens_per_second = (
        round(eval_count / (eval_duration / 1_000_000_000), 2)
        if eval_count and eval_duration else None
    )
    return {
        "load_ms": milliseconds("load_duration"),
        "prompt_eval_ms": milliseconds("prompt_eval_duration"),
        "eval_ms": milliseconds("eval_duration"),
        "total_ms": milliseconds("total_duration"),
        "prompt_tokens": data.get("prompt_eval_count"),
        "output_tokens": data.get("eval_count"),
        "tokens_per_second": tokens_per_second,
        "model": settings.QUESTION_LLM_MODEL,
    }


def _multiple_choice_shape():
    """Schema for one multiple-choice item."""
    text = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "question": text,
            "format": {"type": "string", "enum": ["MCQ"]},
            "choices": {
                "type": "object",
                "properties": {k: text for k in ("A", "B", "C", "D")},
                "required": ["A", "B", "C", "D"],
            },
            "correct_answer": {"type": "string", "enum": ["A", "B", "C", "D"]},
            "explanation": text,
        },
        "required": ["question", "format", "choices", "correct_answer", "explanation"],
    }


def _true_false_shape():
    """Schema for one true/false item; it has no choices field."""
    text = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "question": text,
            "format": {"type": "string", "enum": ["TF"]},
            "correct_answer": {"type": "string", "enum": ["True", "False"]},
            "explanation": text,
        },
        "required": ["question", "format", "correct_answer", "explanation"],
    }


def build_response_schema(format_split):
    """Ollama's JSON schema: every item must match one whole requested format."""
    shapes = {"MCQ": _multiple_choice_shape, "TF": _true_false_shape}
    offered = [
        shapes[fmt]() for fmt in SUPPORTED_FORMATS if format_split.get(fmt)
    ] or [shapes[fmt]() for fmt in SUPPORTED_FORMATS]
    return {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {"anyOf": offered},
            },
        },
        "required": ["questions"],
    }


def _groq_response_schema(format_split):
    """Groq's strict schema for mixed MCQ/TF batches."""
    offered = [fmt for fmt in SUPPORTED_FORMATS if format_split.get(fmt)] or list(SUPPORTED_FORMATS)
    answers = ["A", "B", "C", "D"] if offered == ["MCQ"] else (
        ["True", "False"] if offered == ["TF"] else ["A", "B", "C", "D", "True", "False"]
    )
    return {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "format": {"type": "string", "enum": offered},
                        "choices": {
                            "type": ["object", "null"],
                            "properties": {key: {"type": "string"} for key in "ABCD"},
                            "required": list("ABCD"),
                        },
                        "correct_answer": {"type": "string", "enum": answers},
                        "explanation": {"type": "string"},
                    },
                    "required": [
                        "question", "format", "choices", "correct_answer", "explanation",
                    ],
                },
            },
        },
        "required": ["questions"],
    }


def _format_request(format_split):
    """The prompt line saying how many of each format to write."""
    parts = [
        f"{count} {fmt}" for fmt, count in format_split.items() if count
    ]
    if len(parts) == 1:
        return f"Format: {parts[0]} question(s)."
    return "Formats: " + ", ".join(parts) + " question(s), in that mix."


def _build_prompt(content, thinking_order, format_split, correction=""):
    """The generation prompt, with a retry's correction when given."""
    formats = [fmt for fmt in SUPPORTED_FORMATS if format_split.get(fmt)]
    prompt = PROMPT_TEMPLATE.format(
        count=sum(format_split.values()),
        content=content,
        levels="\n".join(f"- {THINKING_LEVELS[level]}" for level in TIER_LEVELS[thinking_order]),
        item_rules=ITEM_RULES,
        format_request=_format_request(format_split),
        format_instructions="\n".join(FORMAT_INSTRUCTIONS[fmt] for fmt in formats),
        question_schema=", ".join(QUESTION_SCHEMA[fmt] for fmt in formats),
    )
    if correction:
        marker = "\n\nRespond ONLY with valid JSON"
        head, sep, tail = prompt.partition(marker)
        return f"{head}\n\n{correction}{sep}{tail}" if sep else f"{prompt}\n\n{correction}"
    return prompt


_OPEN_QUESTION_OPENERS = (
    "what", "which", "how", "why", "who", "whom", "where", "when",
)


def _validate_question(q, format_type):
    """Reject a structurally unusable question; normalises correct_answer in place."""
    question_text = str(q.get("question") or "").strip()
    if not question_text or "correct_answer" not in q:
        return False

    answer = str(q["correct_answer"]).strip()
    if not answer:
        return False

    if format_type == "MCQ":
        choices = q.get("choices")
        if not isinstance(choices, dict) or not choices:
            return False

        texts = [str(text).strip() for text in choices.values()]
        if any(not text for text in texts):
            return False
        if len({text.casefold() for text in texts}) != len(texts):
            return False
        if len(texts) < 2:
            return False

        if answer in choices:
            q["correct_answer"] = answer
            return True
        for letter, text in choices.items():
            if str(text).strip().casefold() == answer.casefold():
                q["correct_answer"] = letter
                return True
        return False

    if answer.lower() not in ("true", "false"):
        return False
    if question_text.rstrip().endswith("?"):
        return False
    first_word = question_text.split()[0].strip("\"'([{").casefold()
    if first_word in _OPEN_QUESTION_OPENERS:
        return False
    q["correct_answer"] = answer.capitalize()
    return True


def _extract_question_objects(text):
    """Recover the parseable question objects from a broken JSON reply."""
    spans = []
    stack = []
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append(i)
        elif ch == "}":
            if stack:
                start = stack.pop()
                spans.append(text[start:i + 1])

    recovered = []
    for candidate in spans:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "question" in parsed and "correct_answer" in parsed:
            recovered.append(parsed)
    return recovered


def _parse_llm_response(response_text):
    """Parse the model's reply, tolerating code fences and preamble."""
    text = response_text.strip()

    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON found in LLM response: {text[:200]}")

    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError:
        recovered = _extract_question_objects(match.group())
        if not recovered:
            raise
        logger.debug("[Questions] recovered %s question(s) from a malformed model reply", len(recovered))
        return recovered

    if "questions" in parsed:
        return parsed["questions"]
    elif isinstance(parsed, list):
        return parsed
    else:
        return [parsed]


def _ollama_generate(prompt, schema=None, on_metrics=None, format_split=None, on_rate_limit_wait=None):
    """Call the configured LLM provider and return its reply text."""
    if settings.LLM_PROVIDER == "groq":
        offered = format_split or {"MCQ": 1, "TF": 1}
        raw, metrics = groq_generate(
            prompt + (
                "\nFor true/false items, include the required JSON field "
                '"choices": null; for MCQs provide choices A, B, C, and D.'
                if offered.get("TF") else ""
            ),
            model=settings.QUESTION_LLM_MODEL,
            schema=_groq_response_schema(offered) if schema is not None else None,
            temperature=QUESTION_TEMPERATURE,
            max_tokens=QUESTION_NUM_PREDICT,
            timeout=settings.OLLAMA_TIMEOUT,
            on_rate_limit_wait=on_rate_limit_wait,
            use_failed_generation=True,
        )
        if on_metrics:
            on_metrics(metrics)
        return raw

    payload = {
        "model": settings.QUESTION_LLM_MODEL,
        "prompt": prompt,
        "stream": True,
        "keep_alive": getattr(
            settings, "QUESTION_LLM_KEEP_ALIVE", settings.OLLAMA_KEEP_ALIVE
        ),
        "options": {
            "temperature": QUESTION_TEMPERATURE,
            "num_predict": QUESTION_NUM_PREDICT,
        },
    }
    if schema is not None:
        payload["format"] = schema
    response = requests.post(
        f"{settings.OLLAMA_BASE_URL}/api/generate",
        json=payload,
        timeout=settings.OLLAMA_TIMEOUT,
        stream=True,
    )
    response.raise_for_status()
    response_parts = []
    data = None
    for raw_line in response.iter_lines():
        if not raw_line:
            continue
        chunk = json.loads(raw_line)
        if chunk.get("error"):
            raise RuntimeError(str(chunk["error"]))
        response_parts.append(chunk.get("response") or "")
        data = chunk
    if data is None:
        raise ValueError("Ollama returned an empty streaming response.")
    data["response"] = "".join(response_parts)
    _mark_question_model_warm()
    if on_metrics:
        on_metrics(_ollama_metrics(data))
    return data["response"]


def warm_question_model(on_metrics=None):
    """Load the question model before the first real call."""
    if settings.LLM_PROVIDER == "groq":
        metrics = {
            "load_ms": 0.0,
            "prompt_eval_ms": 0.0,
            "eval_ms": 0.0,
            "total_ms": 0.0,
            "prompt_tokens": 0,
            "output_tokens": 0,
            "tokens_per_second": None,
            "model": settings.QUESTION_LLM_MODEL,
            "warm_cache_hit": True,
        }
        if on_metrics:
            on_metrics(metrics)
        return metrics
    with _warm_lock:
        already_warm = (
            _warm_model == settings.QUESTION_LLM_MODEL
            and time.monotonic() < _warm_until
        )
    if already_warm:
        metrics = {
            "load_ms": 0.0,
            "prompt_eval_ms": 0.0,
            "eval_ms": 0.0,
            "total_ms": 0.0,
            "prompt_tokens": 0,
            "output_tokens": 0,
            "tokens_per_second": None,
            "model": settings.QUESTION_LLM_MODEL,
            "warm_cache_hit": True,
        }
        if on_metrics:
            on_metrics(metrics)
        return metrics

    payload = {
        "model": settings.QUESTION_LLM_MODEL,
        "prompt": "",
        "stream": False,
        "keep_alive": getattr(
            settings, "QUESTION_LLM_KEEP_ALIVE", settings.OLLAMA_KEEP_ALIVE
        ),
    }
    try:
        response = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=settings.OLLAMA_TIMEOUT,
        )
        response.raise_for_status()
        metrics = _ollama_metrics(response.json())
        metrics["warm_cache_hit"] = False
        _mark_question_model_warm()
        if on_metrics:
            on_metrics(metrics)
        return metrics
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.warning("Question model warm-up failed: %s", exc)
        return None


def generate_questions(
    content,
    thinking_order,
    format_split,
    on_metrics=None,
    on_error=None,
    on_rate_limit_wait=None,
    correction="",
):
    """One LLM call for one thinking order; returns validated question dicts."""
    prompt = _build_prompt(content, thinking_order, format_split, correction)
    schema = build_response_schema(format_split)
    logger.debug(
        "[Questions] PROMPT SENT TO THE LLM (%s, %s):\n%s",
        thinking_order, " + ".join(f"{n} {fmt}" for fmt, n in format_split.items()), prompt,
    )

    for attempt in range(MAX_UNUSABLE_REPLIES):
        try:
            raw_text = _ollama_generate(
                prompt, schema=schema, on_metrics=on_metrics, format_split=format_split,
                on_rate_limit_wait=on_rate_limit_wait,
            )
            questions = _parse_llm_response(raw_text)

            validated = []
            for q in questions:
                fmt = str(q.get("format") or "").upper()
                if fmt not in SUPPORTED_FORMATS:
                    continue
                if not _validate_question(q, fmt):
                    continue
                q["format"] = fmt
                validated.append(q)

            if validated:
                return validated

            logger.warning(
                "[Questions] model reply had no usable questions (attempt %s of %s)",
                attempt + 1, MAX_UNUSABLE_REPLIES,
            )
            if on_error:
                on_error(attempt + 1, "The model returned no structurally usable questions.")

        except GroqRateLimitError as e:
            logger.warning("[Questions] stopped this batch, Groq rate limit: %s", e)
            if on_error:
                on_error(attempt + 1, str(e))
            break
        except (json.JSONDecodeError, ValueError) as e:
            if on_error:
                on_error(attempt + 1, str(e))
            logger.warning("[Questions] model reply unusable (attempt %s of %s): %s", attempt + 1, MAX_UNUSABLE_REPLIES, e)

    logger.warning("[Questions] gave up: no usable questions after %s attempts", MAX_UNUSABLE_REPLIES)
    return []
