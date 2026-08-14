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
    "decorative_or_noise",
}

NARRATION_CATEGORIES = {"lesson_content"}


def clean_block_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    cleaned = " ".join(lines).strip()
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


def _deterministic_category(block: dict) -> tuple[str, str, float] | None:
    text = block.get("text", "").strip()
    lowered = text.lower()
    normalized = re.sub(r"\s+", " ", lowered)

    visual_bold = bool(block.get("is_bold"))
    visual_strong = visual_bold or (block.get("font_size") is not None and float(block.get("font_size") or 0) >= 12.0)

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

    if text.endswith("?"):
        return "assessment", "Question or task intended to check learner understanding.", 0.88
    if lowered in {"references", "bibliography", "sources", "acknowledgments"} or lowered.startswith(("http://", "https://", "www.")):
        return "reference", "Reference or source information.", 0.96
    if lowered.startswith(("module ", "grade ", "lesson ", "course ", "author:", "date:", "filename:")) and len(text) < 120:
        return "document_metadata", "Administrative document label.", 0.86
    if re.fullmatch(r"\d+\.\d+(?:\.\d+)*\.?\s+[A-Z][A-Za-z0-9 ,:&()/+-]{1,80}", text) and not re.search(r"[.!?]$", text):
        return "lesson_content", "Numbered subtopic heading kept as a learning-object boundary.", 0.78
    if re.fullmatch(r"\d+\.?\s+[A-Z][A-Za-z0-9 ,:&()/+-]{1,80}", text) and not re.search(r"[.!?]$", text):
        return "document_metadata", "Numbered section heading, not narration body.", 0.96
    if visual_strong and re.fullmatch(r"[A-Za-z][A-Za-z0-9 ,&()/-]{1,80}", text) and not re.search(r"[.!?]", text):
        return "lesson_content", "Bold or visually emphasized title-like text retained as a heading/content boundary.", 0.9
    if len(text.split()) <= 4 and not re.search(r"[.!?]", text):
        return "document_metadata", "Short heading or label rather than narration.", 0.72
    return None


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
        for block in batch:
            classified_by_id[block["block_id"]] = _base_classified_block(
                block,
                "lesson_content",
                "Fallback: substantial text kept as lesson content because no rule-based category matched.",
                0.55,
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
