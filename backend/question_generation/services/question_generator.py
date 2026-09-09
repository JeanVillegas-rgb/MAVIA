import json
import re

import requests
from django.conf import settings

# ── Prompt templates ──
# Intentionally SHORT to minimize token usage.
#
# One prompt per Bloom's level the system actually delivers. Steering per level
# rather than per band (LOT/HOT) is what makes per-level coverage achievable at
# all: a prompt that merely asks for "lower-order" questions has no reason to
# ever produce an *apply* question, so that level would simply never fill.
#
# The LLM is still never told about Bloom's taxonomy by name, and the prompt
# never decides the stored label — the classifier assigns that afterwards and
# remains authoritative. These prompts only aim the model at a region.
#
# "create" has no prompt on purpose. MAVIA delivers MCQ and True/False only,
# and a create-level task ("design a…", "compose a…") has no single gradeable
# answer, so it is never generated and never delivered.

GENERATION_LEVELS = ("remember", "understand", "apply", "analyze", "evaluate")

# Formats each level can sensibly take. True/False cannot carry a genuine
# analyse-or-evaluate task — it collapses into a recall check — so the higher
# levels are MCQ only.
LEVEL_FORMATS = {
    "remember": ("MCQ", "TF"),
    "understand": ("MCQ", "TF"),
    "apply": ("MCQ",),
    "analyze": ("MCQ",),
    "evaluate": ("MCQ",),
}

_LEVEL_INSTRUCTIONS = {
    "remember": (
        "Ask the learner to recall a single fact that is stated in the content. "
        "The answer must appear almost word-for-word in the text.\n"
        "Do NOT ask the learner to explain, apply, compare or judge anything."
    ),
    "understand": (
        "Ask the learner to show they grasp what a concept MEANS — to restate it in "
        "different words, interpret it, or say why something described in the content "
        "happens.\n"
        "Do NOT ask for a fact quoted word-for-word, and do NOT ask them to compare or "
        "judge two things."
    ),
    "apply": (
        "Describe a short, concrete, everyday situation NOT mentioned in the content, then "
        "ask the learner to use a rule or idea from the content to decide what happens in "
        "it.\n"
        "The situation must be new; the rule must come from the content.\n"
        "Do NOT ask them to compare two concepts or judge which is better."
    ),
    "analyze": (
        "Ask the learner to take an idea apart: compare or differentiate two things "
        "described in the content, or work out a cause-and-effect relationship between "
        "them.\n"
        "Do NOT ask for a fact stated word-for-word, and do NOT ask which option is better."
    ),
    "evaluate": (
        "Ask the learner to judge, critique or justify — which option is more appropriate "
        "for a stated purpose, and on what grounds.\n"
        "The judgement must be settleable using only the content, with ONE defensible "
        "correct answer.\n"
        "Do NOT ask a question whose answer is a matter of personal opinion."
    ),
}

PROMPT_TEMPLATE = (
    "You are a quiz maker. Given the content below, generate {count} questions.\n"
    "{level_instructions}\n"
    "Every fact you use must come from the content below. Do not introduce facts, "
    "numbers or names that are not in it.\n\n"
    "{examples}\n\n"
    "Format: {format_type}\n\n"
    "Content:\n{content}\n\n"
    "{format_instructions}\n\n"
    "Respond ONLY with valid JSON, no other text. Use this exact structure:\n"
    '{{"questions": [{question_schema}]}}'
)

# ── Few-shot style examples — small local models drift far less when shown
# the target cognitive level instead of only being told about it ──
FEW_SHOT_EXAMPLES = {
    "remember": (
        "Examples of the style (different topic — do NOT reuse these):\n"
        "- What is evaporation?\n"
        "- Which part of a plant takes in water from the soil?"
    ),
    "understand": (
        "Examples of the style (different topic — do NOT reuse these):\n"
        "- Why does a puddle shrink on a sunny day?\n"
        "- What does it mean to say that water vapour is a gas?"
    ),
    "apply": (
        "Examples of the style (different topic — do NOT reuse these):\n"
        "- A pot of water is left boiling on a stove. Which process turns the water "
        "into steam?\n"
        "- Wet clothes are hung outside on a hot, windy day. What will most likely "
        "happen to the water in them?"
    ),
    "analyze": (
        "Examples of the style (different topic — do NOT reuse these):\n"
        "- How does evaporation differ from condensation in the water cycle?\n"
        "- Clouds form high in the sky rather than at ground level. What causes that "
        "difference?"
    ),
    "evaluate": (
        "Examples of the style (different topic — do NOT reuse these):\n"
        "- Which process matters more for forming clouds: evaporation or condensation, "
        "and why?\n"
        "- A farmer waters crops daily but they still die. Which explanation best "
        "justifies why too much water can harm plants?"
    ),
}

FORMAT_INSTRUCTIONS = {
    "MCQ": (
        "Each question must have exactly 4 choices (A, B, C, D). "
        "Only one choice is correct. Distractors should be plausible."
    ),
    "TF": (
        "Each question must be a clear statement that is either True or False. "
        "Do not make it obvious. Include a brief explanation for the correct answer."
    ),
}

QUESTION_SCHEMA = {
    "MCQ": (
        '{"question": "...", "choices": {"A": "...", "B": "...", "C": "...", "D": "..."}, '
        '"correct_answer": "A or B or C or D", "explanation": "brief explanation"}'
    ),
    "TF": (
        '{"question": "statement here", "correct_answer": "True or False", '
        '"explanation": "brief explanation"}'
    ),
}


def _build_prompt(content, bloom_level, format_type, count=1):
    if bloom_level not in _LEVEL_INSTRUCTIONS:
        raise ValueError(
            f"No prompt for Bloom level {bloom_level!r}. "
            f"Generatable levels are: {', '.join(GENERATION_LEVELS)}."
        )
    return PROMPT_TEMPLATE.format(
        count=count,
        content=content,
        format_type=format_type,
        level_instructions=_LEVEL_INSTRUCTIONS[bloom_level],
        format_instructions=FORMAT_INSTRUCTIONS[format_type],
        question_schema=QUESTION_SCHEMA[format_type],
        examples=FEW_SHOT_EXAMPLES[bloom_level],
    )


def _validate_question(q, format_type):
    """Structural validation — reject hallucinated answers before they enter
    the bank (e.g. an MCQ whose correct_answer is 'A, B, C and D').
    Normalizes correct_answer in place when it can be recovered."""
    if "question" not in q or "correct_answer" not in q:
        return False

    answer = str(q["correct_answer"]).strip()

    if format_type == "MCQ":
        choices = q.get("choices")
        if not isinstance(choices, dict) or not choices:
            return False
        if answer in choices:
            q["correct_answer"] = answer
            return True
        # LLM sometimes answers with the choice text instead of the letter
        for letter, text in choices.items():
            if str(text).strip().lower() == answer.lower():
                q["correct_answer"] = letter
                return True
        return False

    # TF
    if answer.lower() in ("true", "false"):
        q["correct_answer"] = answer.capitalize()
        return True
    return False


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
        print(f"  Recovered {len(recovered)} question(s) from malformed JSON response")
        return recovered

    if "questions" in parsed:
        return parsed["questions"]
    elif isinstance(parsed, list):
        return parsed
    else:
        return [parsed]


def _ollama_generate(prompt):
    """Call Ollama over HTTP using the project's existing settings."""
    response = requests.post(
        f"{settings.OLLAMA_BASE_URL}/api/generate",
        json={
            "model": settings.QUESTION_LLM_MODEL,
            "prompt": prompt,
            "stream": False,
            "keep_alive": settings.OLLAMA_KEEP_ALIVE,
            "options": {
                # Deliberately NOT 0.0. Greedy decoding is the right setting for
                # the one-output-per-input content variations, but this pipeline
                # asks for several DISTINCT questions per level and then
                # deduplicates: at temperature 0 repeated calls return identical
                # text, so overgeneration would collect duplicates and the dedup
                # pass would delete them. Educational question-generation studies
                # commonly run 0.7-0.9; this sits below that because MAVIA's
                # grounding requirements are stricter. Grounding is enforced by
                # the prompt ceiling and the validation pass, not by temperature.
                "temperature": settings.QUESTION_LLM_TEMPERATURE,
                # generous ceiling — a batch of 5 MCQs (4 choices + explanation
                # each) can run past 1000 tokens and get cut off mid-JSON
                "num_predict": 2048,
            },
        },
        timeout=settings.OLLAMA_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["response"]


def generate_questions(content, bloom_level, format_type, count=1, max_retries=3):
    """
    Generate questions using the local LLM.

    The returned questions carry no classification — the prompt only steers
    toward a Bloom level, it does not decide one. The classifier assigns the
    authoritative label later, in the post-generation pass, and a question that
    lands on a different level than the prompt aimed at keeps the classifier's
    answer.

    Args:
        content:        text content to generate questions from
        bloom_level:    one of GENERATION_LEVELS — which prompt to steer with
        format_type:    "MCQ" or "TF"
        count:          number of questions to generate
        max_retries:    retry on JSON parse failures

    Returns:
        list of question dicts
    """
    prompt = _build_prompt(content, bloom_level, format_type, count)

    for attempt in range(max_retries):
        try:
            raw_text = _ollama_generate(prompt)
            questions = _parse_llm_response(raw_text)

            validated = []
            for q in questions:
                if not _validate_question(q, format_type):
                    continue
                q["format"] = format_type
                validated.append(q)

            if validated:
                return validated

        except (json.JSONDecodeError, ValueError) as e:
            print(f"  Attempt {attempt + 1}/{max_retries} failed: {e}")
            continue

    print(f"  WARNING: Failed to generate after {max_retries} attempts")
    return []
