"""Deterministic lesson-block labels used by the PDF pipeline."""

import re


CLASSIFICATION_CATEGORIES = (
    "lesson_content",
    "assessment",
    "answer_key",
    "reference",
    "document_metadata",
    "navigation",
    "needs_review",
    "noise",
)


def normalized_heading_label(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").casefold()).strip()


def is_structural_metadata_label(text):
    """Return whether a heading only identifies document structure."""
    label = normalized_heading_label(text)
    return bool(label and re.fullmatch(
        r"(?:module|unit|chapter|lesson|week|quarter|section|part)\s+"
        r"(?:\d+|[ivxlcdm]+)(?:\s+.+)?",
        label,
    ))


def is_non_lesson_section(text):
    label = text.strip().rstrip(":").casefold()
    if re.match(r"^(?:lesson|activity)\s+\d+\b", label):
        return True
    label = re.sub(r"^\d+[.)]\s+", "", label)
    return label in {
        "learning objectives", "objective", "objectives", "quiz",
        "assessment", "exercise", "answer key", "references",
        "check your understanding", "quick check questions",
        "review questions",
    }


def classify_text(text):
    text = (text or "").strip()
    lowered = text.casefold()
    if not text or re.fullmatch(r"[\W_]+", text):
        return "noise"
    if re.fullmatch(r"(?:page\s*)?\d+(?:\s*/\s*\d+)?", text, re.I):
        return "noise"
    if text.endswith("?"):
        return "assessment"
    if re.search(
        r"\b(?:choose|calculate|classify|compare|define|describe|explain|"
        r"identify|list|match|solve|write)\b",
        text,
        re.I,
    ):
        return "assessment"
    if re.search(r"\b(?:quiz|test|assessment|exercise|worksheet|practice questions?)\b", text, re.I):
        return "assessment"
    if re.fullmatch(r"(?:(?:expected|suggested|sample|correct)\s+)?answers?", lowered):
        return "answer_key"
    if lowered in {"reference", "references", "source", "sources", "bibliography"}:
        return "reference"
    if re.match(r"^(?:https?://|www\.|doi:)", text, re.I):
        return "reference"
    if re.search(r"\b(?:copyright|all rights reserved|published by|printed by)\b", text, re.I):
        return "document_metadata"
    if re.fullmatch(r".+\.{3,}\s*\d+", text):
        return "navigation"
    if re.match(r"^(?:module|grade|lesson|course|author|date|filename)\b", text, re.I):
        return "document_metadata"
    if re.search(
        r"\b(?:is defined as|refers to|for example|because|results in|"
        r"causes|consists of|difference between)\b",
        text,
        re.I,
    ):
        return "lesson_content"
    if len(text.split()) >= 8 and re.search(r"[.!;:]$", text):
        return "lesson_content"
    return "needs_review"


def classify_block(block):
    text = (block.get("text") or "").strip()
    category = classify_text(text)

    question_heading = bool(
        category == "assessment"
        and text.endswith("?")
        and block.get("is_bold")
        and (block.get("dominant_font_size") or 0) >= 12
        and block.get("line_count", 1) <= 2
        and len(text.split()) <= 10
        and not re.match(r"^\s*(?:question\s*)?\d+[.)-]", text, re.I)
    )
    if question_heading:
        category = "needs_review"
    return {
        **block,
        "starts_excluded_section": is_non_lesson_section(text),
        "category": category,
        "include_in_matching": category == "lesson_content",
        "include_in_narration": category == "lesson_content",
    }


def classify_blocks(blocks):
    classified = [classify_block(block) for block in blocks]

    # A numbered lesson heading can also be written as a question, for
    # example ``1. What is Matter?``.  Looking at that block alone is
    # ambiguous, so keep ordinary numbered questions as assessments.  Promote
    # it only when the document also contains a numbered, non-question lesson
    # heading with the same typography and explanatory prose follows it.  This
    # uses the document's structure instead of special-casing a subject name.
    for index, block in enumerate(classified):
        text = (block.get("text") or "").strip()
        if not (
            block.get("category") == "assessment"
            and re.match(r"^\s*\d+[.)]\s+.+\?\s*$", text)
            and block.get("is_bold")
            and block.get("line_count", 1) <= 2
        ):
            continue

        font_size = block.get("dominant_font_size") or 0
        peer_indexes = [
            peer_index
            for peer_index, peer in enumerate(classified)
            if peer_index != index
            and peer.get("category") != "assessment"
            and peer.get("is_bold")
            and re.match(r"^\s*\d+[.)]\s+\S", (peer.get("text") or "").strip())
            and abs((peer.get("dominant_font_size") or 0) - font_size) <= 1.5
        ]
        later_peers = [peer_index for peer_index in peer_indexes if peer_index > index]
        boundary = min(later_peers) if later_peers else len(classified)
        has_explanation = any(
            candidate.get("category") == "lesson_content"
            for candidate in classified[index + 1:boundary]
        )
        if peer_indexes and has_explanation:
            block["category"] = "needs_review"
            block["include_in_matching"] = False
            block["include_in_narration"] = False

    return classified


def detect_instructional_document_role(classified_blocks):
    lesson = sum(block.get("category") == "lesson_content" for block in classified_blocks)
    assessments = sum(block.get("category") == "assessment" for block in classified_blocks)
    return "assessment" if assessments >= 3 and assessments > lesson else "lesson"
