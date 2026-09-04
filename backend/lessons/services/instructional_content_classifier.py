from __future__ import annotations

import re

import fitz


CLASSIFICATION_CATEGORIES = {
    "lesson_content",
    "learning_objective",
    "assessment",
    "teacher_note",
    "concept_metadata",
    "document_metadata",
    "table_header",
    "reference",
    "answer_key",
    "navigation",
    "needs_review",
    "decorative_or_noise",
}

NARRATION_CATEGORIES = {"lesson_content"}

EXCLUDED_HEADING_LABELS = {
    "acknowledgment",
    "acknowledgments",
    "acknowledgement",
    "acknowledgements",
    "about the author",
    "about the authors",
    "answer key",
    "answers",
    "bibliography",
    "copyright",
    "dedication",
    "general instructions",
    "glossary",
    "index",
    "publisher information",
    "references",
    "sources",
    "suggested answers",
    "table of contents",
}

ASSESSMENT_HEADING_LABELS = {
    "assessment",
    "assessments",
    "check your knowledge",
    "check your understanding",
    "evaluation",
    "evaluations",
    "exercise",
    "exercises",
    "practice questions",
    "quiz",
    "quizzes",
    "review questions",
    "test",
    "tests",
    "teacher check",
    "knowledge check",
    "comprehension check",
    "self check",
    "ask",
    "worksheet",
    "worksheets",
}


def _normalized_label(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").casefold()).strip()


def _normalized_repeated_text(text: str) -> str:
    normalized = _normalized_label(text)
    return re.sub(r"\b\d+\b", "#", normalized)


def _is_question_or_assessment(text: str) -> bool:
    stripped = re.sub(r"\s+", " ", text or "").strip()
    label = _normalized_label(stripped)
    if not label:
        return False
    if label in ASSESSMENT_HEADING_LABELS:
        return True
    if re.search(r"\b(?:questions?|quizzes?|tests?|assessments?|exercises?|worksheets?)$", label):
        return True
    if re.match(r"^\s*[QA]\s*:", stripped, flags=re.IGNORECASE):
        return True
    if stripped.endswith("?"):
        return True
    if re.search(r"_{3,}", stripped):
        return True
    if re.match(r"^\s*(?:directions?|instructions?)\s*:", stripped, flags=re.IGNORECASE):
        return True
    if re.match(r"^\s*\d+[.)]\s+", stripped) and re.search(
        r"_{3,}|\b(?:answer|calculate|choose|classify|compare|define|describe|discuss|"
        r"explain|fill|give|identify|list|match|name|select|solve|state|write)\b",
        stripped,
        flags=re.IGNORECASE,
    ):
        return True
    if len(re.findall(r"(?:^|\s)[A-Da-d][.)]\s+", stripped)) >= 2:
        return True
    if re.match(r"^(?:q\d+|question\s+\d+)\b", label):
        return True
    if re.search(r"(?:^|\s)[A-D][.)]\s+\S", stripped) and re.search(r"\?", stripped):
        return True
    prompt = re.sub(r"^(?:[•●▪\-*]\s*)?(?:\d+[.)]|[A-Za-z][.)])\s*", "", stripped)
    prompt_label = _normalized_label(prompt)
    instruction_phrases = (
        "answer the following",
        "choose the correct",
        "classify the following",
        "complete the",
        "fill in",
        "match the",
        "multiple choice",
        "true or false",
        "write your answer",
    )
    return any(phrase in prompt_label for phrase in instruction_phrases)


def _is_symbolic_relationship(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    return bool(
        normalized
        and len(normalized) <= 180
        and re.search(r"\S\s*(?:\u2192|->|=>)\s*\S", normalized)
    )


def _is_teacher_facing_editorial_text(text: str) -> bool:
    label = _normalized_label(text)
    tokens = set(label.split())
    if not tokens:
        return False
    authoring_terms = {
        "content",
        "document",
        "format",
        "info",
        "information",
        "lesson",
        "notes",
        "read",
        "sequence",
        "write",
        "written",
    }
    evaluation_terms = {"actual", "like", "more", "random", "rather", "should", "want"}
    if {"you", "want"}.issubset(tokens) and len(tokens & authoring_terms) >= 2:
        return True
    return bool(
        "teacher" in tokens
        and "notes" in tokens
        and tokens & authoring_terms
        and tokens & evaluation_terms
    )


def _positive_instructional_evidence(text: str) -> bool:
    stripped = re.sub(r"\s+", " ", text or "").strip()
    if not stripped or _is_question_or_assessment(stripped):
        return False
    if re.match(r"^[•●▪\-*]\s+\S", stripped) and len(stripped.split()) >= 3:
        return True
    instructional_patterns = (
        r"\b(?:is|are)(?:\s+\w+ly)?\s+(?:a|an|the)\b",
        r"\bis defined as\b",
        r"\brefers? to\b",
        r"\bfor example\b",
        r"\bfor instance\b",
        r"\bsuch as\b",
        r"\bbecause\b",
        r"\bresults? in\b",
        r"\bcauses?\b",
        r"\bchanges? (?:into|from)\b",
        r"\bbecomes?\s+(?:a|an|the)\b",
        r"\b(?:contains?|includes?|consists? of)\b",
        r"\bdifference between\b",
        r"\bprocess of\b",
        r"\bfirst\b.+\bthen\b",
        r"\bformula\b",
    )
    if any(re.search(pattern, stripped, flags=re.IGNORECASE) for pattern in instructional_patterns):
        return True
    # Ordinary explanatory prose remains valid even when it does not use a
    # dictionary-style definition phrase.
    return len(stripped.split()) >= 8 and bool(re.search(r"[.!;:]$", stripped))


def _repeated_page_chrome_ids(blocks: list[dict]) -> set[int]:
    occurrences: dict[tuple[str, str], set[int]] = {}
    ids_by_key: dict[tuple[str, str], list[int]] = {}
    total_pages = max((int(block.get("page") or 0) for block in blocks), default=0)
    if total_pages < 2:
        return set()

    for block in blocks:
        text = (block.get("text") or "").strip()
        bbox = block.get("bbox") or (0, 0, 0, 0)
        page_height = float(block.get("page_height") or 0)
        if not text or not page_height or len(text.split()) > 14:
            continue
        position = None
        if float(bbox[1]) <= page_height * 0.10:
            position = "header"
        elif float(bbox[3]) >= page_height * 0.90:
            position = "footer"
        if position is None:
            continue
        normalized = _normalized_repeated_text(text)
        if not normalized:
            continue
        key = (position, normalized)
        occurrences.setdefault(key, set()).add(int(block.get("page") or 0))
        ids_by_key.setdefault(key, []).append(block["block_id"])

    repeated_ids = set()
    minimum_pages = 2 if total_pages <= 4 else 3
    for key, pages in occurrences.items():
        if len(pages) >= minimum_pages:
            repeated_ids.update(ids_by_key[key])
    return repeated_ids


def clean_block_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    # Preserve PyMuPDF's physical line boundaries. Joining with spaces erased
    # vocabulary rows, answer choices, and paragraph formatting, producing one
    # very long learning-object string.
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"^G\s+(?=[A-Z])", "", cleaned)
    return cleaned.strip()


def _text_from_pdf_block(block: dict) -> str:
    lines = []
    for line in block.get("lines", []):
        line_text = "".join(span.get("text") or "" for span in line.get("spans", []))
        if line_text.strip():
            lines.append(line_text)
    return clean_block_text("\n".join(lines))


def find_captioned_figure_regions(page, page_dict: dict | None = None) -> list[dict]:
    """Locate captioned graphics and the text visually contained by them."""
    page_dict = page_dict or page.get_text("dict")
    raw_page_rect = getattr(page, "rect", None)
    if raw_page_rect is None:
        return []
    page_rect = fitz.Rect(raw_page_rect)
    page_area = max(page_rect.width * page_rect.height, 1)
    text_blocks = []
    visual_rects = []

    for block in page_dict.get("blocks", []):
        bbox = block.get("bbox")
        if not bbox:
            continue
        rect = fitz.Rect(bbox)
        if block.get("type") == 0:
            text = _text_from_pdf_block(block)
            if text:
                text_blocks.append({"text": text, "rect": rect})
        elif block.get("type") == 1:
            visual_rects.append(rect)

    get_drawings = getattr(page, "get_drawings", None)
    if callable(get_drawings):
        try:
            visual_rects.extend(fitz.Rect(item["rect"]) for item in get_drawings() if item.get("rect"))
        except (RuntimeError, TypeError, ValueError):
            pass

    usable_visuals = []
    for rect in visual_rects:
        area = max(rect.width, 0) * max(rect.height, 0)
        if area <= 0 or area / page_area >= 0.75:
            continue
        usable_visuals.append(rect)

    regions = []
    captions = [
        item
        for item in text_blocks
        if re.match(r"^\s*(?:figure|fig\.)\s*\d+\s*[.:]", item["text"], flags=re.IGNORECASE)
    ]
    for caption in captions:
        caption_rect = caption["rect"]
        nearby_visuals = [
            rect
            for rect in usable_visuals
            if rect.y0 < caption_rect.y0
            and rect.y1 <= caption_rect.y0 + 4
            and caption_rect.y0 - rect.y1 <= page_rect.height * 0.35
        ]
        if not nearby_visuals:
            continue

        nearest_bottom = max(rect.y1 for rect in nearby_visuals)
        cluster_gap = max(18.0, page_rect.height * 0.03)
        visual_cluster = [rect for rect in nearby_visuals if rect.y1 >= nearest_bottom - cluster_gap]
        visual_bbox = fitz.Rect(visual_cluster[0])
        for rect in visual_cluster[1:]:
            visual_bbox.include_rect(rect)
        if visual_bbox.width < page_rect.width * 0.15 or visual_bbox.height < 18:
            continue

        title_candidates = [
            item
            for item in text_blocks
            if item["rect"].y1 <= visual_bbox.y0 + 2
            and visual_bbox.y0 - item["rect"].y1 <= page_rect.height * 0.08
            and len(item["text"].split()) <= 14
            and not re.search(r"[.!?]$", item["text"])
        ]
        figure_title = max(title_candidates, key=lambda item: item["rect"].width, default=None)
        crop_bbox = fitz.Rect(visual_bbox)
        crop_bbox.include_rect(caption_rect)
        if figure_title:
            crop_bbox.include_rect(figure_title["rect"])
        crop_bbox = fitz.Rect(
            max(page_rect.x0, crop_bbox.x0 - 10),
            max(page_rect.y0, crop_bbox.y0 - 6),
            min(page_rect.x1, crop_bbox.x1 + 10),
            min(page_rect.y1, crop_bbox.y1 + 5),
        )

        contained_items = []
        for item in text_blocks:
            center = (item["rect"].x0 + item["rect"].x1) / 2, (item["rect"].y0 + item["rect"].y1) / 2
            if crop_bbox.contains(fitz.Point(*center)):
                contained_items.append(item)
                crop_bbox.include_rect(item["rect"])
        crop_bbox = fitz.Rect(
            max(page_rect.x0, crop_bbox.x0 - 4),
            max(page_rect.y0, crop_bbox.y0 - 4),
            min(page_rect.x1, crop_bbox.x1 + 4),
            min(page_rect.y1, crop_bbox.y1 + 4),
        )
        regions.append(
            {
                "bbox": tuple(crop_bbox),
                "caption": caption["text"],
                "visible_text": "\n".join(item["text"] for item in contained_items),
            }
        )
    return regions


def extract_pdf_text_blocks(file_path: str) -> list[dict]:
    blocks = []
    block_id = 1
    document = fitz.open(file_path)
    try:
        for page_index, page in enumerate(document, start=1):
            page_dict = page.get_text("dict")
            figure_regions = find_captioned_figure_regions(page, page_dict)
            page_rect = getattr(page, "rect", None)
            for block_index, block in enumerate(page_dict.get("blocks", [])):
                if block.get("type") != 0:
                    continue

                text_lines = []
                bold_found = False
                font_sizes = []
                colors = []
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    line_text_segments = []
                    for span in spans:
                        span_text = span.get("text") or ""
                        if not span_text:
                            continue
                        line_text_segments.append(span_text)
                        flags = int(span.get("flags") or 0)
                        font_name = (span.get("font") or "").casefold()
                        if flags & 2 or "bold" in font_name:
                            bold_found = True
                        size = float(span.get("size") or 0.0)
                        if size > 0:
                            font_sizes.append(size)
                        color = span.get("color")
                        if color is not None:
                            if isinstance(color, (list, tuple)):
                                try:
                                    normalized_color = tuple(int(channel) for channel in color)
                                except (TypeError, ValueError):
                                    normalized_color = color
                                colors.append(normalized_color)
                            elif isinstance(color, (int, float)):
                                colors.append(int(color))
                            else:
                                try:
                                    colors.append(tuple(color))
                                except TypeError:
                                    colors.append(color)
                    line_text = "".join(line_text_segments)
                    if line_text.strip():
                        text_lines.append(line_text)

                text = clean_block_text("\n".join(text_lines))
                if not text:
                    continue

                average_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else None
                dominant_color = colors[0] if colors else None
                bbox = tuple(float(value) for value in (block.get("bbox") or (0, 0, 0, 0)))
                block_center = fitz.Point((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
                is_figure_text = any(fitz.Rect(region["bbox"]).contains(block_center) for region in figure_regions)
                blocks.append(
                    {
                        "block_id": block_id,
                        "page": page_index,
                        "block_index": block_index,
                        "text": text,
                        "line_count": len(text_lines),
                        "is_bold": bool(bold_found),
                        "font_size": round(average_font_size, 2) if average_font_size else None,
                        "text_color": dominant_color,
                        "bbox": bbox,
                        "page_width": float(page_rect.width) if page_rect is not None else None,
                        "page_height": float(page_rect.height) if page_rect is not None else None,
                        "is_figure_text": is_figure_text,
                    }
                )
                block_id += 1
    finally:
        document.close()
    return blocks


def _base_classified_block(block: dict, category: str, reason: str, confidence: float = 0.9) -> dict:
    classified = {
        "block_id": block["block_id"],
        "page": block.get("page"),
        "text": block.get("text", ""),
        "category": category,
        "include_in_narration": category in NARRATION_CATEGORIES,
        "confidence": max(0.0, min(float(confidence), 1.0)),
        "reason": reason,
        "source": "teacher_pdf",
    }
    for key in (
        "block_index",
        "line_count",
        "is_bold",
        "font_size",
        "text_color",
        "bbox",
        "page_width",
        "page_height",
        "is_figure_text",
    ):
        if key in block:
            classified[key] = block[key]
    return classified


def _is_overlapping_extraction_duplicate(block: dict, earlier_blocks: list[dict]) -> bool:
    """Reject duplicate draw/extraction layers, not intentional repeated teaching."""
    bbox = block.get("bbox")
    if not bbox:
        return False
    try:
        rect = fitz.Rect(bbox)
    except (TypeError, ValueError):
        return False
    if rect.is_empty:
        return False
    for earlier in earlier_blocks:
        if earlier.get("page") != block.get("page") or not earlier.get("bbox"):
            continue
        try:
            earlier_rect = fitz.Rect(earlier["bbox"])
        except (TypeError, ValueError):
            continue
        intersection = rect & earlier_rect
        overlap = intersection.get_area() / max(min(rect.get_area(), earlier_rect.get_area()), 1)
        if overlap >= 0.90:
            return True
    return False


def _deterministic_category(block: dict) -> tuple[str, str, float] | None:
    text = block.get("text", "").strip()
    lowered = text.lower()
    normalized = re.sub(r"\s+", " ", lowered)

    visual_bold = bool(block.get("is_bold"))
    visual_strong = visual_bold or (block.get("font_size") is not None and float(block.get("font_size") or 0) >= 12.0)

    if block.get("is_repeated_page_chrome"):
        return "document_metadata", "Repeated running header or footer.", 1.0
    if block.get("is_figure_text"):
        return "decorative_or_noise", "Text belongs to a captioned figure rather than the lesson body.", 1.0
    if not text or re.fullmatch(r"(?:page\s*)?\d+(?:\s*/\s*\d+)?", lowered):
        return "decorative_or_noise", "Isolated page number or empty extraction artifact.", 1.0
    if re.search(r"\bpage\s+\d+\b", normalized) and len(text.split()) <= 8:
        return "document_metadata", "Document header or page label.", 1.0
    if re.fullmatch(r"[\W_]+", text):
        return "decorative_or_noise", "Punctuation-only extraction artifact.", 1.0
    if len(text) <= 2:
        return "decorative_or_noise", "Too short to be instructional content.", 0.98

    label = _normalized_label(text)
    if _is_teacher_facing_editorial_text(text):
        return "teacher_note", "Teacher-facing editorial or authoring comment.", 0.98
    if re.fullmatch(r"(?:(?:expected|suggested|sample|correct)\s+)?answers?", label):
        return "answer_key", "Expected or supplied answer is separate from instructional content.", 1.0
    if _is_question_or_assessment(text):
        return "assessment", "Question, exercise, or assessment instruction.", 0.98
    if label in {"answer key", "answers", "suggested answers"}:
        return "answer_key", "Answer-key material is separate from instructional content.", 1.0
    if label in EXCLUDED_HEADING_LABELS or lowered.startswith(("http://", "https://", "www.", "doi:")):
        return "reference", "Reference or source information.", 0.96
    if re.search(r"\bISBN(?:-1[03])?\s*:?\s*(?:97[89][-\s]?)?[0-9Xx][0-9Xx\s-]{8,}\b", text, flags=re.IGNORECASE):
        return "document_metadata", "ISBN or publication identifier.", 1.0
    if re.search(r"\b(?:copyright|all rights reserved|published by|printed by|no part of this publication)\b|©", lowered):
        return "document_metadata", "Copyright or publisher information.", 1.0
    if re.fullmatch(r".+\.{3,}\s*\d+", text):
        return "navigation", "Table-of-contents entry with a page number.", 1.0
    if re.fullmatch(r"(?:next|previous|back|home|click here|tap here)", label):
        return "navigation", "Document navigation control.", 1.0
    if re.match(
        r"^(?:these terms|the following vocabulary)\b",
        normalized,
        flags=re.IGNORECASE,
    ):
        return "concept_metadata", "Vocabulary-section introduction, not a concept explanation.", 0.96
    if lowered.startswith(("module ", "grade ", "lesson ", "course ", "author:", "date:", "filename:")) and len(text) < 120:
        return "document_metadata", "Administrative document label.", 0.86
    if _is_symbolic_relationship(text):
        return "lesson_content", "Compact symbolic relationship retained as instructional content.", 0.9
    if re.match(r"^\s*[•●▪\-*]\s*\S", text):
        return "lesson_content", "Authored bullet retained under its surrounding lesson concept.", 0.9
    if re.fullmatch(r"\d+\.\d+(?:\.\d+)*\.?\s+[A-Z][A-Za-z0-9 ,:&()/+-]{1,80}", text) and not re.search(r"[.!?]$", text):
        return "lesson_content", "Numbered subtopic heading kept as a learning-object boundary.", 0.78
    if re.fullmatch(r"\d+\.?\s+[A-Z][A-Za-z0-9 ,:&()/+-]{1,80}", text) and not re.search(r"[.!?]$", text):
        if visual_strong:
            return "lesson_content", "Visually emphasized numbered lesson heading retained as a boundary.", 0.9
        return "document_metadata", "Unemphasized numbered structural label, not narration body.", 0.86
    if visual_strong and re.fullmatch(r"[A-Za-z][A-Za-z0-9 ,&()/-]{1,80}", text) and not re.search(r"[.!?]", text):
        return "lesson_content", "Bold or visually emphasized title-like text retained as a heading/content boundary.", 0.9
    if len(text.split()) <= 4 and not re.search(r"[.!?]", text):
        return "document_metadata", "Short heading or label rather than narration.", 0.72
    return None


def classify_instructional_blocks(blocks: list[dict], batch_size: int = 20) -> list[dict]:
    classified_by_id = {}
    ambiguous = []
    seen_text: dict[str, list[dict]] = {}

    repeated_page_chrome_ids = _repeated_page_chrome_ids(blocks)

    for original_block in blocks:
        block = {
            **original_block,
            "is_repeated_page_chrome": original_block["block_id"] in repeated_page_chrome_ids,
        }
        if block["is_repeated_page_chrome"]:
            classified_by_id[block["block_id"]] = _base_classified_block(
                block,
                "document_metadata",
                "Repeated running header or footer.",
                1.0,
            )
            continue
        text_key = block.get("text", "").casefold()
        earlier_matches = seen_text.get(text_key, [])
        if _is_overlapping_extraction_duplicate(block, earlier_matches):
            classified_by_id[block["block_id"]] = _base_classified_block(
                block,
                "decorative_or_noise",
                "Exact duplicate extraction artifact.",
                1.0,
            )
            continue
        seen_text.setdefault(text_key, []).append(block)

        deterministic = _deterministic_category(block)
        if deterministic:
            category, reason, confidence = deterministic
            classified_by_id[block["block_id"]] = _base_classified_block(block, category, reason, confidence)
        else:
            ambiguous.append(block)

    for start in range(0, len(ambiguous), batch_size):
        batch = ambiguous[start : start + batch_size]
        for block in batch:
            is_instructional = _positive_instructional_evidence(block.get("text", ""))
            classified_by_id[block["block_id"]] = _base_classified_block(
                block,
                "lesson_content" if is_instructional else "needs_review",
                (
                    "Substantial explanatory text with positive instructional evidence."
                    if is_instructional
                    else "No positive instructional or exclusion evidence; teacher review required."
                ),
                0.65 if is_instructional else 0.4,
            )

    return [classified_by_id[block["block_id"]] for block in blocks if block["block_id"] in classified_by_id]


def detect_instructional_document_role(classified_blocks: list[dict]) -> str:
    """Identify question-heavy documents before lesson-content fallback runs."""
    assessment_blocks = [
        block for block in classified_blocks if block.get("category") == "assessment"
    ]
    lesson_blocks = [
        block for block in classified_blocks if block.get("category") == "lesson_content"
    ]
    if not assessment_blocks:
        return "lesson"
    if not lesson_blocks:
        return "assessment"

    assessment_section_count = sum(
        bool(
            re.search(
                r"\b(?:true\s*(?:/|or)\s*false|multiple\s+choice|fill\s+in|matching)\b",
                block.get("text") or "",
                flags=re.IGNORECASE,
            )
        )
        for block in assessment_blocks
    )
    longest_lesson_block = max(
        (len((block.get("text") or "").split()) for block in lesson_blocks),
        default=0,
    )
    if assessment_section_count >= 2 and longest_lesson_block <= 25:
        return "assessment"

    assessment_words = sum(len((block.get("text") or "").split()) for block in assessment_blocks)
    lesson_words = sum(len((block.get("text") or "").split()) for block in lesson_blocks)
    classified_words = assessment_words + lesson_words
    assessment_ratio = assessment_words / classified_words if classified_words else 0.0
    # Worksheets often contain short declarative True/False prompts that look
    # like lesson sentences in isolation. Once several explicit assessment
    # blocks dominate the document, treat those short statements as part of the
    # assessment instead of allowing them to turn the PDF into lesson material.
    if len(assessment_blocks) >= 3 and assessment_ratio >= 0.60:
        return "assessment"
    return "mixed"


def split_classified_blocks(classified_blocks: list[dict]) -> dict:
    return {
        "lesson_content_blocks": [b for b in classified_blocks if b.get("category") == "lesson_content"],
        "learning_objectives": [b for b in classified_blocks if b.get("category") == "learning_objective"],
        "assessments": [b for b in classified_blocks if b.get("category") == "assessment"],
        "teacher_notes": [b for b in classified_blocks if b.get("category") == "teacher_note"],
        "concept_metadata_blocks": [b for b in classified_blocks if b.get("category") == "concept_metadata"],
        "ignored_blocks": [
            b for b in classified_blocks
            if b.get("category") in {
                "answer_key",
                "decorative_or_noise",
                "document_metadata",
                "navigation",
                "needs_review",
                "reference",
                "table_header",
            }
        ],
    }
