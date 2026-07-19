import json
import re

import requests
from django.conf import settings

# ── Prompt templates ──
# Intentionally SHORT to minimize token usage.
# These nudge toward a cognitive level without teaching Bloom's taxonomy.

PROMPT_TEMPLATES = {
    "easy": (
        "You are a quiz maker. Given the content below, generate {count} questions.\n"
        "The questions should be SIMPLE and DIRECT. Ask the learner to recall "
        "a specific fact, name, or definition EXACTLY as stated in the content.\n"
        "Do NOT create scenarios. Do NOT ask 'what would happen if'. "
        "Just ask WHAT, WHO, WHEN, or HOW MANY.\n\n"
        "{examples}\n\n"
        "Format: {format_type}\n\n"
        "Content:\n{content}\n\n"
        "{format_instructions}\n\n"
        "Respond ONLY with valid JSON, no other text. Use this exact structure:\n"
        '{{"questions": [{question_schema}]}}'
    ),
    "medium": (
        "You are a quiz maker. Given the content below, generate {count} questions.\n"
        "The questions should present a SCENARIO or ask the learner to COMPARE, "
        "DIFFERENTIATE, or APPLY a concept from the content to a new situation.\n\n"
        "{examples}\n\n"
        "Format: {format_type}\n\n"
        "Content:\n{content}\n\n"
        "{format_instructions}\n\n"
        "Respond ONLY with valid JSON, no other text. Use this exact structure:\n"
        '{{"questions": [{question_schema}]}}'
    ),
    "hard": (
        "You are a quiz maker. Given the content below, generate {count} questions.\n"
        "The questions should ask the learner to EVALUATE, JUDGE, or JUSTIFY "
        "which approach or concept is more appropriate and why, based on the content.\n\n"
        "{examples}\n\n"
        "Format: {format_type}\n\n"
        "Content:\n{content}\n\n"
        "{format_instructions}\n\n"
        "Respond ONLY with valid JSON, no other text. Use this exact structure:\n"
        '{{"questions": [{question_schema}]}}'
    ),
}

# ── Strict variants — used when rebalancing a short difficulty level ──
# The normal prompts drift toward apply-level questions; these leave the
# LLM less room to wander.

STRICT_PROMPT_TEMPLATES = {
    "easy": (
        "You are a quiz maker. Given the content below, generate {count} questions.\n"
        "Ask ONLY for direct recall of facts stated word-for-word in the content.\n"
        "Use question stems like \"What is...\", \"How many...\", \"Name the...\".\n"
        "For True/False, state a single fact from the content as-is or slightly altered.\n"
        "Do NOT create scenarios or hypotheticals. Do NOT ask \"what would happen if\". "
        "Do NOT ask the learner to compare, apply, or judge anything.\n\n"
        "{examples}\n\n"
        "Format: {format_type}\n\n"
        "Content:\n{content}\n\n"
        "{format_instructions}\n\n"
        "Respond ONLY with valid JSON, no other text. Use this exact structure:\n"
        '{{"questions": [{question_schema}]}}'
    ),
    "medium": (
        "You are a quiz maker. Given the content below, generate {count} questions.\n"
        "Each question MUST present a short concrete scenario and ask the learner "
        "to APPLY a concept from the content to predict what happens.\n"
        "Use stems like \"What happens when...\" or \"A student does X. What will result?\".\n"
        "Do NOT ask for simple recall of a stated fact. "
        "Do NOT use stems like \"Which approach is more appropriate\", \"Which is best\", "
        "or \"Which of the following would be most...\" — never ask the learner to "
        "judge, rank, or justify.\n\n"
        "{examples}\n\n"
        "Format: {format_type}\n\n"
        "Content:\n{content}\n\n"
        "{format_instructions}\n\n"
        "Respond ONLY with valid JSON, no other text. Use this exact structure:\n"
        '{{"questions": [{question_schema}]}}'
    ),
    "hard": (
        "You are a quiz maker. Given the content below, generate {count} questions.\n"
        "Each question MUST ask the learner to EVALUATE, JUDGE, or JUSTIFY: "
        "which approach or concept is more appropriate, most important, or best — and why.\n"
        "Use stems like \"Which is more important...\", \"What is the best...\", "
        "\"Why is X more appropriate than Y...\".\n"
        "Do NOT ask for simple recall. Do NOT ask a plain application question.\n\n"
        "{examples}\n\n"
        "Format: {format_type}\n\n"
        "Content:\n{content}\n\n"
        "{format_instructions}\n\n"
        "Respond ONLY with valid JSON, no other text. Use this exact structure:\n"
        '{{"questions": [{question_schema}]}}'
    ),
}

# ── Few-shot style examples — small local models drift far less when shown
# the target cognitive level instead of only being told about it ──
FEW_SHOT_EXAMPLES = {
    "easy": (
        "Examples of the style (different topic — do NOT reuse these):\n"
        "- What is evaporation?\n"
        "- How many planets orbit the Sun?\n"
        "- Name the process plants use to make their own food."
    ),
    "medium": (
        "Examples of the style (different topic — do NOT reuse these):\n"
        "- A puddle disappears after a sunny day. What process occurred?\n"
        "- A student breathes on a cold window and it fogs up. What is forming on the glass?"
    ),
    "hard": (
        "Examples of the style (different topic — do NOT reuse these):\n"
        "- A farmer waters crops daily but they still die. Which explanation best "
        "justifies why too much water can harm plants?\n"
        "- Which process matters more for forming clouds: evaporation or condensation? Why?"
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


def _build_prompt(content, difficulty, format_type, count=1, strict=False):
    templates = STRICT_PROMPT_TEMPLATES if strict else PROMPT_TEMPLATES
    template = templates[difficulty]
    return template.format(
        count=count,
        content=content,
        format_type=format_type,
        format_instructions=FORMAT_INSTRUCTIONS[format_type],
        question_schema=QUESTION_SCHEMA[format_type],
        examples=FEW_SHOT_EXAMPLES[difficulty],
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

    parsed = json.loads(match.group())

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
                "temperature": 0.7,
                "num_predict": 1024,
            },
        },
        timeout=settings.OLLAMA_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["response"]


def generate_questions(content, difficulty, format_type, count=1, max_retries=3,
                       strict=False):
    """
    Generate questions using the local LLM.

    Args:
        content:     text content to generate questions from
        difficulty:  "easy", "medium", or "hard"
        format_type: "MCQ" or "TF"
        count:       number of questions to generate
        max_retries: retry on JSON parse failures
        strict:      use the stricter prompt variant (for rebalancing)

    Returns:
        list of question dicts
    """
    prompt = _build_prompt(content, difficulty, format_type, count, strict=strict)

    for attempt in range(max_retries):
        try:
            raw_text = _ollama_generate(prompt)
            questions = _parse_llm_response(raw_text)

            validated = []
            for q in questions:
                if not _validate_question(q, format_type):
                    continue
                q["format"] = format_type
                q["intended_difficulty"] = difficulty
                validated.append(q)

            if validated:
                return validated

        except (json.JSONDecodeError, ValueError) as e:
            print(f"  Attempt {attempt + 1}/{max_retries} failed: {e}")
            continue

    print(f"  WARNING: Failed to generate after {max_retries} attempts")
    return []
