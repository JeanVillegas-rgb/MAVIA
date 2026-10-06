import hashlib
import json
import logging
import re
import threading
import time

import requests
from django.conf import settings
from config.groq_client import GroqRateLimitError, generate as groq_generate

logger = logging.getLogger(__name__)

QUESTION_GENERATOR_VERSION = 2
QUESTION_TEMPERATURE = 0.7
# Room for five questions with explanations per call, plus the reasoning
# gpt-oss spends from the same budget: a reply cut off mid-question is
# broken JSON.
QUESTION_NUM_PREDICT = 3072
# How many parseable-but-unusable replies to accept before giving up on a
# call. See the comment in generate_questions: the third attempt almost never
# rescues one, and each is a full LLM round trip.
MAX_UNUSABLE_REPLIES = 2
_warm_lock = threading.Lock()
_warm_model = ""
_warm_until = 0.0


def _mark_question_model_warm():
    global _warm_model, _warm_until
    with _warm_lock:
        _warm_model = settings.QUESTION_LLM_MODEL
        # Slightly shorter than the default Ollama keep-alive so the local
        # state never intentionally outlives the server-side residency window.
        _warm_until = time.monotonic() + 25 * 60

# ── Prompt templates ──
# Intentionally SHORT to minimize token usage.
# Keyed by thinking order, and deliberately phrased in terms of the cognitive
# work each question demands rather than how "hard" it is. The LLM is never
# told about Bloom's taxonomy — the classifier assigns the actual level after
# generation, and these prompts only steer the model toward the right region.
#
#   LOT covers remember / understand / apply
#   HOT covers analyze / evaluate
#
# There are no "strict" retry variants any more. LOT and HOT are wide enough
# that ordinary prompt drift stays inside the intended bucket, so the pipeline
# no longer regenerates to correct it.

# The grounding rule, shared by both thinking orders. Worded after
# course/variant_generator.py, which has carried the same prohibition since
# the version generator was written and does not drift off-source the way
# this one did. Stated as a prohibition, not an invitation: "answerable from
# the content" told the model what a good question looks like and left it
# free to use anything it knew.
_GROUNDING_RULE = (
    "Use ONLY the facts stated in the content below. Do not add facts, "
    "terms, examples, numbers, causes or categories the content does not "
    "state, even if you know them to be true. If the content does not "
    "settle something, do not ask about it.\n"
    "Every choice must use words and ideas from the content. A wrong choice "
    "must be wrong because the content says otherwise, not because it names "
    "something the lesson never mentions.\n"
    "Write the question and the correct answer using only words that appear in "
    "the content below, plus simple question words (what, which, who, why, how, "
    "when, where). Do not frame the question with phrases the content does not "
    "use, such as 'according to', 'mentioned' or 'which statement best describes'; "
    "ask about the content directly.\n"
    # Learners hear a figure's description; they never see its layout. A
    # question about which row or column holds what tests the picture, not the
    # lesson -- and the extracted text has lost the layout, so it cannot be
    # verified either.
    "Do not ask about the visual layout of a figure or table, such as rows, "
    "columns, positions or labels. Ask about what it teaches.\n"
)

PROMPT_TEMPLATES = {
    "LOT": (
        "You are a quiz maker. Given the content below, generate {count} questions.\n"
        + _GROUNDING_RULE +
        "Each question must be answerable DIRECTLY from the content. Ask the learner to "
        "recall a stated fact, show they understand what a concept means, or use a stated "
        "rule in a straightforward case.\n"
        "Do NOT ask the learner to compare two things, weigh trade-offs, judge which "
        "option is better, or justify a choice.\n\n"
        "{examples}\n\n"
        "{format_request}\n\n"
        "Content:\n{content}\n\n"
        "{format_instructions}\n\n"
        "Respond ONLY with valid JSON, no other text. Every question object must\n"
        "carry a \"format\" field saying which kind it is. Use this exact structure:\n"
        '{{"questions": [{question_schema}]}}'
    ),
    "HOT": (
        "You are a quiz maker. Given the content below, generate {count} questions.\n"
        + _GROUNDING_RULE +
        "Each question must require reasoning BEYOND recall or direct application. Build "
        "it by asking the learner to combine two or more facts the content states: "
        "compare two things it describes, work out a cause and effect it implies, or "
        "judge which of two stated options fits a situation.\n"
        "The answer must follow from the stated facts. Do not ask about a cause, "
        "comparison or consequence the content gives you no facts for.\n"
        "The question must still have ONE defensible correct answer.\n\n"
        "{examples}\n\n"
        "{format_request}\n\n"
        "Content:\n{content}\n\n"
        "{format_instructions}\n\n"
        "Respond ONLY with valid JSON, no other text. Every question object must\n"
        "carry a \"format\" field saying which kind it is. Use this exact structure:\n"
        '{{"questions": [{question_schema}]}}'
    ),
}

# ── Few-shot style examples — small local models drift far less when shown
# the target cognitive level instead of only being told about it ──
# Patterns, not questions on a subject: bracketed parts stand for the
# content's own words, so the examples fit any lesson and pull no topic in.
# Outside the brackets they use only words the grounding gate never asks the
# lesson to contain (what, which, why, how, when, more, than ...); an example
# framed with "explanation best justifies" taught exactly the wording the gate
# rejects.
FEW_SHOT_EXAMPLES = {
    "LOT": (
        "Examples of the style (patterns only -- fill the brackets with the content's own words):\n"
        "- What is [a term the content defines]?\n"
        "- Which [kind of thing the content lists] has [a property the content states]?\n"
        "- What does [something the content describes] do when [a situation the content states]?"
    ),
    "HOT": (
        "Examples of the style (patterns only -- fill the brackets with the content's own words):\n"
        "- Why does [something the content describes] [an action the content states] "
        "when [a condition the content states]?\n"
        "- Which is more [a quality the content states]: [one thing the content describes] "
        "or [another thing it describes]? Why?\n"
        "- If [a condition the content states] were true, what would [something the "
        "content describes] do?"
    ),
}

FORMAT_INSTRUCTIONS = {
    "MCQ": (
        "Each question must have exactly 4 choices (A, B, C, D). "
        "Only one choice is correct. Each wrong choice must name a specific "
        "misconception a learner could hold about THIS content -- something "
        "the content shows to be wrong. Do not invent an option the lesson "
        "never mentions: an unfamiliar word is not a distractor, it is a "
        "giveaway."
    ),
    "TF": (
        "Each question must be a clear statement that is either True or False. "
        "Do not make it obvious. Include a brief explanation for the correct answer."
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

# Formats the pipeline can actually assess. An item claiming anything else is
# dropped rather than coerced -- guessing at what the model meant would put an
# ungradeable question into the bank.
SUPPORTED_FORMATS = ("MCQ", "TF")


def question_bank_fingerprint(content, distribution):
    """Identify the exact source and generation contract for a question bank."""
    payload = {
        "version": QUESTION_GENERATOR_VERSION,
        "content": content,
        "model": settings.QUESTION_LLM_MODEL,
        "distribution": distribution,
        "prompts": PROMPT_TEMPLATES,
        "examples": FEW_SHOT_EXAMPLES,
        "format_instructions": FORMAT_INSTRUCTIONS,
        "schemas": QUESTION_SCHEMA,
        "temperature": QUESTION_TEMPERATURE,
        "num_predict": QUESTION_NUM_PREDICT,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ollama_metrics(data):
    """Convert Ollama nanosecond timings into trace-friendly values."""
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
    """One whole multiple-choice item: a stem, four named options, a letter."""
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
            # Constrained at decode time so an out-of-range answer cannot be
            # emitted. It does not stop a *wrong* letter -- "D" is legal even
            # when D says "plasma" -- which is what _validate_question and the
            # CRAG gate are for.
            "correct_answer": {"type": "string", "enum": ["A", "B", "C", "D"]},
            "explanation": text,
        },
        # The explanation is required so the model has to state why its answer
        # follows from the content. A model that cannot write one usually could
        # not ground the question either.
        "required": ["question", "format", "choices", "correct_answer", "explanation"],
    }


def _true_false_shape():
    """One whole true/false item. It has no ``choices`` field at all.

    Deliberately absent rather than optional: a field the model is never
    offered is one it cannot fill with null.
    """
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
    """JSON schema handed to Ollama so the response cannot be malformed.

    Ollama compiles this into a grammar that constrains decoding token by
    token, so the shape is not checked after the fact -- it cannot be written
    wrong in the first place. That is what lets one call return a mix of
    multiple-choice and true/false questions.

    Every item must match one *whole* shape, via ``anyOf``. A single merged
    shape had to leave ``choices`` optional, because a true/false item has
    none -- and optional told the model it could skip the options. It skipped
    them every time: measured on concept 532, an MCQ-only call returned three
    questions and none survived validation, each with ``choices: null``. HOT
    was the worst hit, being multiple-choice only, so every HOT call produced
    nothing usable and its questions arrived only as LOT-call output the Bloom
    classifier happened to relabel.

    Only the requested formats are offered. The model reaches for true/false
    when left free -- asked for two multiple-choice and one true/false, it
    returned three true/false -- and a true/false question a learner can guess
    right half the time is weak evidence of mastery. Pinning the shapes keeps
    the mix the distribution asked for.

    ``anyOf`` support depends on the model's grammar conversion. Verified on
    llama3.2:3b; re-test before trusting it on another model.
    """
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
    """One strict shape for mixed MCQ/TF batches on Groq.

    Groq rejects the overlapping ``anyOf`` branches in the Ollama schema.
    A nullable choices object covers both formats; the existing per-format
    validator still requires four usable choices for each MCQ.
    """
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
    """The human-readable half of the same instruction the schema encodes."""
    parts = [
        f"{count} {fmt}" for fmt, count in format_split.items() if count
    ]
    if len(parts) == 1:
        return f"Format: {parts[0]} question(s)."
    return "Formats: " + ", ".join(parts) + " question(s), in that mix."


def _build_prompt(content, thinking_order, format_split, correction=""):
    """The generation prompt, optionally carrying a corrective retry's feedback.

    ``correction`` is written by the grounding gate (services/grounding.py)
    when a previous attempt produced questions the source does not support. It
    goes *after* the content and before the output contract, so the model reads
    the material first and the specific mistakes to avoid last.
    """
    formats = [fmt for fmt in SUPPORTED_FORMATS if format_split.get(fmt)]
    prompt = PROMPT_TEMPLATES[thinking_order].format(
        count=sum(format_split.values()),
        content=content,
        format_request=_format_request(format_split),
        format_instructions="\n".join(FORMAT_INSTRUCTIONS[fmt] for fmt in formats),
        question_schema=", ".join(QUESTION_SCHEMA[fmt] for fmt in formats),
        examples=FEW_SHOT_EXAMPLES[thinking_order],
    )
    if correction:
        marker = "\n\nRespond ONLY with valid JSON"
        head, sep, tail = prompt.partition(marker)
        return f"{head}\n\n{correction}{sep}{tail}" if sep else f"{prompt}\n\n{correction}"
    return prompt


# A true/false item is a statement the learner judges, so an open question is
# not one: "True" answers nothing about "what is the shape of the particles?".
#
# Two markers, because neither alone is enough. A trailing question mark is the
# reliable one -- measured on topic 276, Q108 read "In the arrangement that
# shows particles close together but able to move, what is the shape of the
# particles?", whose interrogative sits mid-sentence behind a subordinate
# clause, so nothing about its first word gives it away. The opener list then
# catches a stem the model wrote without punctuation.
#
# Containing a wh-word is deliberately not a marker: "Water takes the shape of
# whatever container holds it" is a perfectly good statement.
_OPEN_QUESTION_OPENERS = (
    "what", "which", "how", "why", "who", "whom", "where", "when",
)


def _validate_question(q, format_type):
    """Reject a question that is structurally unusable, whatever it says.

    Correctness is not decidable here -- that needs the source text, and is
    what services/grounding.py does. This checks only what the question
    itself settles, and normalises ``correct_answer`` in place when it can.
    """
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
        # A blank option is unreadable aloud, and a repeated one means the
        # learner either cannot be wrong or cannot be right.
        if any(not text for text in texts):
            return False
        if len({text.casefold() for text in texts}) != len(texts):
            return False
        if len(texts) < 2:
            return False

        if answer in choices:
            q["correct_answer"] = answer
            return True
        # The model often answers with the choice text instead of the letter.
        for letter, text in choices.items():
            if str(text).strip().casefold() == answer.casefold():
                q["correct_answer"] = letter
                return True
        return False

    # TF
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
    """Recover individual question objects from text that isn't valid JSON
    as a whole — e.g. the response got truncated by the token limit mid
    object, or the model dropped a comma between two objects. Questions sit
    nested inside {"questions": [...]}, so this records every balanced
    {...} span at any depth (via a stack, honoring quoted strings) and
    parses each independently — a truncated or comma-less object simply
    fails to close or fails to parse, and is skipped without sinking the
    objects around it."""
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
    """
    Extract JSON from LLM response.
    Local models are messy — they sometimes wrap JSON in markdown
    code blocks or add preamble text.
    """
    text = response_text.strip()

    # strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # find the outermost JSON object
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
    """Generate questions through the selected LLM provider.

    ``schema`` constrains the reply at decode time. Temperature stays where it
    was: the schema governs shape, not wording, so lowering it would cost
    question variety without preventing a single malformed response.
    """
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
            # Every item is validated per format after this, so a batch Groq
            # rejected over one item's shape is checked here, not discarded.
            use_failed_generation=True,
        )
        if on_metrics:
            on_metrics(metrics)
        return raw

    payload = {
        "model": settings.QUESTION_LLM_MODEL,
        "prompt": prompt,
        # Chunks keep slow CPU generation from looking like an idle HTTP
        # connection. They are joined before parsing, preserving the API.
        "stream": True,
        "keep_alive": getattr(
            settings, "QUESTION_LLM_KEEP_ALIVE", settings.OLLAMA_KEEP_ALIVE
        ),
        "options": {
            "temperature": QUESTION_TEMPERATURE,
            # generous ceiling — a batch of MCQs (4 choices + explanation
            # each) can run past 1000 tokens and get cut off mid-JSON
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
    """Load the configured question model without generating lesson content."""
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
        # Warming is an optimization, never a prerequisite. The real request
        # remains authoritative and will surface its own failure normally.
        logger.warning("Question model warm-up failed: %s", exc)
        return None


def generate_questions(
    content,
    thinking_order,
    format_split,
    max_retries=3,
    on_metrics=None,
    on_error=None,
    on_rate_limit_wait=None,
    correction="",
):
    """
    Generate one thinking order's questions in a single LLM call.

    The returned questions carry no classification — the prompt only steers
    toward a thinking order, it does not decide one. The Bloom classifier
    assigns the authoritative label later, in the post-generation pass.

    Args:
        content:        text content to generate questions from
        thinking_order: "LOT" or "HOT" — which prompt to steer with
        format_split:   {"MCQ": 2, "TF": 1} — how many of each kind to ask for
        max_retries:    retry on JSON parse failures
        correction:     grounding feedback from a rejected previous attempt,
                        appended to the prompt (see services/grounding.py)

    Returns:
        list of question dicts, each labelled with the format it actually is
    """
    prompt = _build_prompt(content, thinking_order, format_split, correction)
    schema = build_response_schema(format_split)

    # A reply that parses but yields nothing usable gets one more try, not the
    # full budget. Measured on topic 276: 74 calls exhausted all three
    # attempts with zero JSON parse failures -- the model had written
    # something structurally unusable every time, and a third round of the
    # same prompt at the same temperature almost never rescues that. Malformed
    # JSON keeps the full budget below: that is a transport failure, not the
    # model being unable to write the question.
    unusable_replies = 0
    for attempt in range(max_retries):
        try:
            raw_text = _ollama_generate(
                prompt, schema=schema, on_metrics=on_metrics, format_split=format_split,
                on_rate_limit_wait=on_rate_limit_wait,
            )
            questions = _parse_llm_response(raw_text)

            validated = []
            for q in questions:
                # The item's own format decides how it is validated. Asking for
                # two MCQs and being handed a usable true/false question is not
                # a failure -- the split is a request, not a contract.
                fmt = str(q.get("format") or "").upper()
                if fmt not in SUPPORTED_FORMATS:
                    continue
                if not _validate_question(q, fmt):
                    continue
                q["format"] = fmt
                validated.append(q)

            if validated:
                return validated

            unusable_replies += 1
            logger.warning(
                "[Questions] model reply had no usable questions (attempt %s of %s)",
                attempt + 1, max_retries,
            )
            if on_error:
                on_error(attempt + 1, "The model returned no structurally usable questions.")
            if unusable_replies >= MAX_UNUSABLE_REPLIES:
                break

        except GroqRateLimitError as e:
            logger.warning("[Questions] stopped this batch, Groq rate limit: %s", e)
            if on_error:
                on_error(attempt + 1, str(e))
            break
        except (json.JSONDecodeError, ValueError) as e:
            if on_error:
                on_error(attempt + 1, str(e))
            logger.warning("[Questions] model reply unusable (attempt %s of %s): %s", attempt + 1, max_retries, e)
            continue

    logger.warning("[Questions] gave up: no usable questions after %s attempts", max_retries)
    return []
