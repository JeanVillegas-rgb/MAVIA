"""
Shared normalization helpers for GeneratedQuestion data.

GeneratedQuestion belongs to a separate app (question_generation) and stores
`choices` as an unconstrained JSONField (dict-keyed, list, or None) and
`correct_answer` as free text that may or may not match one of the choices.

These helpers give both the service layer (LessonPackageService) and the DRF
serializers (LessonNodeSerializer) one canonical way to turn that into:
  - a flat list of up to 4 choice strings (choice_texts)
  - a single-letter A/B/C/D label for the correct answer (answer_label)

Import from here instead of re-deriving this logic in either place.
"""


def choice_texts(question):
    """Return this question's choices as a flat list of up to 4 strings."""
    choices = question.choices or []

    if isinstance(choices, dict):
        return [
            str(choices.get(label, ""))
            for label in ("A", "B", "C", "D")
            if choices.get(label)
        ]

    if question.question_format == "TF" and not choices:
        return ["True", "False"]

    return [str(choice) for choice in choices[:4]]


def answer_label(question):
    """Return the correct answer as an A/B/C/D label where possible.

    Falls back to the raw stored value if it can't be matched against the
    question's choices (correct_answer is free text, not FK-constrained).
    """
    answer = (question.correct_answer or "").strip()
    if answer.upper() in {"A", "B", "C", "D"}:
        return answer.upper()

    choices = question.choices or []
    if question.question_format == "TF":
        if answer.lower() == "true":
            return "A"
        if answer.lower() == "false":
            return "B"

    if isinstance(choices, dict):
        for label in ("A", "B", "C", "D"):
            if str(choices.get(label, "")).strip().lower() == answer.lower():
                return label
        return answer

    for index, choice in enumerate(choices[:4]):
        if str(choice).strip().lower() == answer.lower():
            return "ABCD"[index]
    return answer
