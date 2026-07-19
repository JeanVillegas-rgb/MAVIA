from __future__ import annotations

import re
import textwrap

import fitz

from .llm_client import extract_json_from_text, get_llm_client


CLASSIFICATION_CATEGORIES = {
    "lesson_content",
    "learning_objective",
    "assessment",
    "teacher_note",
    "concept_metadata",
    "document_metadata",
    "table_header",
    "reference",
    "decorative_or_noise",
}

NARRATION_CATEGORIES = {"lesson_content"}

EXCLUDED_CATEGORIES = CLASSIFICATION_CATEGORIES - NARRATION_CATEGORIES - {"learning_objective", "assessment"}


def clean_block_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    cleaned = " ".join(lines).strip()
    cleaned = re.sub(r"^G\s+(?=[A-Z])", "", cleaned)
    return cleaned.strip()


def extract_pdf_text_blocks(file_path: str) -> list[dict]:
    blocks = []
    block_id = 1
    document = fitz.open(file_path)
    try:
        for page_index, page in enumerate(document, start=1):
            page_dict = page.get_text("dict")
            for block_index, block in enumerate(page_dict.get("blocks", [])):
                if block.get("type") != 0:
                    continue
                text_lines = []
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    line_text = "".join(span.get("text", "") for span in spans)
                    if line_text.strip():
                        text_lines.append(line_text)
                text = clean_block_text("\n".join(text_lines))
                if not text:
                    continue
                blocks.append(
                    {
                        "block_id": block_id,
                        "page": page_index,
                        "block_index": block_index,
                        "text": text,
                        "line_count": len(text_lines),
                    }
                )
                block_id += 1
    finally:
        document.close()
    return blocks


def _base_classified_block(block: dict, category: str, reason: str, confidence: float = 0.9) -> dict:
    return {
        "block_id": block["block_id"],
        "page": block.get("page"),
        "text": block.get("text", ""),
        "category": category,
        "include_in_narration": category in NARRATION_CATEGORIES,
        "confidence": max(0.0, min(float(confidence), 1.0)),
        "reason": reason,
        "source": "teacher_pdf",
    }


def _deterministic_category(block: dict) -> tuple[str, str, float] | None:
    text = block.get("text", "").strip()
    lowered = text.lower()
    normalized = re.sub(r"\s+", " ", lowered)
    line_count = int(block.get("line_count") or 1)

    if not text or re.fullmatch(r"(?:page\s*)?\d+(?:\s*/\s*\d+)?", lowered):
        return "decorative_or_noise", "Isolated page number or empty extraction artifact.", 1.0
    if re.search(r"\bpage\s+\d+\b", normalized) and any(
        marker in normalized for marker in ("mavia", "sample lesson", "lesson material")
    ):
        return "document_metadata", "Document header or page label.", 1.0
    if normalized.startswith("mavia ") or normalized in {"mavia", "sample lesson content", "sample lesson material"}:
        return "document_metadata", "Document title/header label.", 1.0
    if re.fullmatch(r"[\W_]+", text):
        return "decorative_or_noise", "Punctuation-only extraction artifact.", 1.0
    if len(text) <= 2:
        return "decorative_or_noise", "Too short to be instructional content.", 0.98

    table_headers = {
        "concept",
        "student-friendly meaning",
        "why it matters",
        "concept student-friendly meaning why it matters",
        "source support",
        "prerequisite cues",
    }
    instructional_table_keywords = (
        "example",
        "description",
        "characteristic",
        "property",
        "meaning",
        "function",
        "part",
        "type",
        "state",
        "solid",
        "liquid",
        "gas",
        "compare",
        "difference",
        "similarity",
    )
    if (
        line_count >= 3
        and len(text.split()) >= 8
        and any(keyword in lowered for keyword in instructional_table_keywords)
        and not any(marker in lowered for marker in ("key concepts for extraction", "prerequisite cue", "edge-scoring"))
    ):
        return "lesson_content", "Instructional table or chart text kept as lesson content.", 0.82
    if lowered in table_headers or lowered.startswith(("concept ", "key concepts for extraction")):
        return "concept_metadata", "Concept extraction table or metadata heading.", 0.96
    if "why it matters" in lowered and len(text) < 160:
        return "concept_metadata", "Concept metadata label.", 0.94
    if "prerequisite cue" in lowered or "foundation concept" in lowered or "learner path" in lowered:
        return "concept_metadata", "Prerequisite or DAG support metadata.", 0.94
    if lowered.startswith(("learners should", "the concepts of shape", "understanding the three states")):
        return "concept_metadata", "Prerequisite or learner-path support statement.", 0.94
    if "edge-scoring algorithm" in lowered or "concept nodes" in lowered:
        return "concept_metadata", "Internal learner-path or concept extraction support.", 0.96
    if lowered.startswith(("teacher note", "teacher review note", "implementation note", "testing note", "internal testing")):
        return "teacher_note", "Teacher-only or implementation note.", 0.98
    if lowered.startswith("purpose:") or "local llm may" in lowered or "suitable for testing" in lowered or "mavia testing" in lowered:
        return "teacher_note", "Implementation/testing note.", 0.95
    if lowered.startswith(("quick check", "quiz", "review questions", "exercise", "activity")):
        return "assessment", "Assessment or learner task heading.", 0.93
    if text.endswith("?") or lowered.startswith(("why ", "what ", "how ", "classify ", "identify ", "explain ")):
        return "assessment", "Question or task intended to check learner understanding.", 0.88
    objective_starts = (
        "define ",
        "describe ",
        "relate ",
        "classify ",
        "identify ",
        "compare ",
        "explain ",
        "differentiate ",
        "state ",
    )
    objective_text = re.sub(r"^[A-Z]\s+", "", text).strip()
    objective_lowered = objective_text.lower()
    if lowered.startswith(("learning objective", "objectives", "at the end of", "learners will", "students will")):
        return "learning_objective", "Learning objective statement.", 0.9
    if re.match(r"^[A-Z]\s+(?:define|describe|relate|classify|identify|compare|explain|differentiate|state)\b", text):
        return "learning_objective", "Learning objective bullet from PDF extraction.", 0.98
    if objective_lowered.startswith(objective_starts) and len(objective_text.split()) <= 14:
        return "learning_objective", "Short objective-style action statement.", 0.86
    if lowered in {"references", "bibliography", "sources", "acknowledgments"} or lowered.startswith(("http://", "https://", "www.")):
        return "reference", "Reference or source information.", 0.96
    if lowered.startswith(("module ", "grade ", "lesson ", "course ", "author:", "date:", "filename:")) and len(text) < 120:
        return "document_metadata", "Administrative document label.", 0.86
    if re.fullmatch(r"\d+\.\d+(?:\.\d+)*\.?\s+[A-Z][A-Za-z0-9 ,:&()/+-]{1,80}", text) and not re.search(r"[.!?]$", text):
        return "lesson_content", "Numbered subtopic heading kept as a learning-object boundary.", 0.78
    if re.fullmatch(r"\d+\.?\s+[A-Z][A-Za-z0-9 ,:&()/+-]{1,80}", text) and not re.search(r"[.!?]$", text):
        return "document_metadata", "Numbered section heading, not narration body.", 0.96
    if len(text.split()) <= 4 and not re.search(r"[.!?]", text):
        return "document_metadata", "Short heading or label rather than narration.", 0.72
    if len(text.split()) >= 12 and re.search(r"[.!?]$", text):
        return "lesson_content", "Substantial explanatory sentence or paragraph.", 0.9
    return None


def _classification_prompt(blocks: list[dict]) -> str:
    block_lines = "\n".join(
        f'{{"block_id": {block["block_id"]}, "page": {block.get("page")}, "text": {block.get("text", "")!r}}}'
        for block in blocks
    )
    return textwrap.dedent(
        f"""
        Classify teacher-uploaded PDF text blocks for a blind-friendly lesson narration pipeline.

        Return JSON only:
        {{
          "blocks": [
            {{
              "block_id": 1,
              "category": "lesson_content",
              "include_in_narration": true,
              "confidence": 0.95,
              "reason": "short reason"
            }}
          ]
        }}

        Categories:
        lesson_content, learning_objective, assessment, teacher_note, concept_metadata,
        document_metadata, table_header, reference, decorative_or_noise.

        Rules:
        - Do not rewrite, paraphrase, correct, shorten, or add text.
        - Classify each block into exactly one category.
        - Narration is true only for lesson_content.
        - learning_objective is stored separately and not narrated by default.
        - Questions, quick checks, and tasks are assessment, not narration.
        - Teacher/developer notes, concept tables, "why it matters", prerequisite cues,
          and metadata must not be narrated.

        Blocks:
        {block_lines}
        """
    ).strip()


def _validate_llm_block(original_by_id: dict[int, dict], item: dict) -> dict | None:
    try:
        block_id = int(item.get("block_id"))
    except (TypeError, ValueError):
        return None
    original = original_by_id.get(block_id)
    if original is None:
        return None

    category = item.get("category")
    if category not in CLASSIFICATION_CATEGORIES:
        category = "lesson_content"

    try:
        confidence = float(item.get("confidence", 0.65))
    except (TypeError, ValueError):
        confidence = 0.65

    return {
        "block_id": original["block_id"],
        "page": original.get("page"),
        "text": original.get("text", ""),
        "category": category,
        "include_in_narration": category in NARRATION_CATEGORIES,
        "confidence": max(0.0, min(confidence, 1.0)),
        "reason": str(item.get("reason") or "Classified by local text model.")[:240],
        "source": "teacher_pdf",
    }


def _classify_batch_with_llm(blocks: list[dict]) -> list[dict] | None:
    client = get_llm_client()
    response = client.generate_text(_classification_prompt(blocks), max_tokens=2200, timeout=240)
    output = response.get("text") if isinstance(response, dict) else None
    data = extract_json_from_text(output) if output else response
    returned = data.get("blocks") if isinstance(data, dict) else None
    if not isinstance(returned, list):
        return None
    original_by_id = {block["block_id"]: block for block in blocks}
    validated = [_validate_llm_block(original_by_id, item) for item in returned if isinstance(item, dict)]
    validated = [item for item in validated if item is not None]
    return validated if validated else None


def classify_instructional_blocks(blocks: list[dict], batch_size: int = 20) -> list[dict]:
    classified_by_id = {}
    ambiguous = []
    seen_text = set()

    for block in blocks:
        text_key = block.get("text", "").casefold()
        if text_key in seen_text:
            classified_by_id[block["block_id"]] = _base_classified_block(
                block,
                "decorative_or_noise",
                "Exact duplicate extraction artifact.",
                1.0,
            )
            continue
        seen_text.add(text_key)

        deterministic = _deterministic_category(block)
        if deterministic:
            category, reason, confidence = deterministic
            classified_by_id[block["block_id"]] = _base_classified_block(block, category, reason, confidence)
        else:
            ambiguous.append(block)

    for start in range(0, len(ambiguous), batch_size):
        batch = ambiguous[start : start + batch_size]
        try:
            llm_items = _classify_batch_with_llm(batch)
        except Exception:
            llm_items = None
        if not llm_items:
            llm_items = [
                _base_classified_block(
                    block,
                    "lesson_content",
                    "Fallback: substantial text kept as lesson content because classification failed.",
                    0.55,
                )
                for block in batch
            ]
        for item in llm_items:
            classified_by_id[item["block_id"]] = item

        missing_ids = {block["block_id"] for block in batch} - {item["block_id"] for item in llm_items}
        for block in batch:
            if block["block_id"] in missing_ids:
                classified_by_id[block["block_id"]] = _base_classified_block(
                    block,
                    "lesson_content",
                    "Fallback: model skipped this block, so substantial text was kept.",
                    0.5,
                )

    return [classified_by_id[block["block_id"]] for block in blocks if block["block_id"] in classified_by_id]


def split_classified_blocks(classified_blocks: list[dict]) -> dict:
    return {
        "lesson_content_blocks": [b for b in classified_blocks if b.get("category") == "lesson_content"],
        "learning_objectives": [b for b in classified_blocks if b.get("category") == "learning_objective"],
        "assessments": [b for b in classified_blocks if b.get("category") == "assessment"],
        "teacher_notes": [b for b in classified_blocks if b.get("category") == "teacher_note"],
        "concept_metadata_blocks": [b for b in classified_blocks if b.get("category") == "concept_metadata"],
        "ignored_blocks": [
            b for b in classified_blocks
            if b.get("category") in {"document_metadata", "table_header", "reference", "decorative_or_noise"}
        ],
    }
