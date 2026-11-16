"""Build ordered learning-object drafts from classified PDF blocks."""

HEADING_LABELS = {"title", "section_header", "heading"}
EXCLUDED_CATEGORIES = {"assessment", "other"}


def join_part_text(parts):
    """Combine blocks while preserving separate numbered and bulleted items."""
    result = ""
    for part in parts:
        text = (part.get("text") or "").strip()
        if not text:
            continue
        separator = "\n" if result and part.get("docling_label") == "list_item" else " "
        result = f"{result}{separator}{text}" if result else text
    return result


def _is_heading(block):
    return block.get("docling_label") in HEADING_LABELS


def _draft_from_blocks(title, parts, source_blocks, section_title):
    content = join_part_text(parts)
    if not content:
        return None
    return {
        "draft_id": "",
        "type": "text",
        "title": title,
        "section_title": section_title or title,
        "content": content,
        "source_text": content,
        "parts": [
            {
                "type": "text",
                "text": part["text"],
                "docling_label": part.get("docling_label", ""),
            }
            for part in parts
        ],
        "source_block_ids": [
            block.get("block_id")
            for block in source_blocks
            if block.get("block_id") is not None
        ],
        "source_page": source_blocks[0].get("page"),
    }


def _visual_drafts(visuals, start_order):
    drafts = []
    for index, visual in enumerate(visuals or [], start=start_order):
        image_url = (visual.get("image_url") or "").strip()
        if not image_url:
            continue
        caption = (visual.get("caption") or "").strip()
        parts = [{
            "type": "image",
            "image_url": image_url,
            "page": visual.get("page"),
        }]
        if caption:
            parts.append({"type": "text", "text": caption})
        drafts.append({
            "draft_id": f"image_{index}",
            "type": "image",
            "title": caption or f"Image on page {visual.get('page', '')}".strip(),
            "section_title": "",
            "content": caption,
            "source_text": caption,
            "parts": parts,
            "source_block_ids": [],
            "source_page": visual.get("page"),
        })
    return drafts


def build_learning_objects(blocks, visuals=()):
    """Group lesson-content blocks under their nearest authored heading."""
    lesson_blocks = [
        block for block in blocks
        if (block.get("text") or "").strip()
        and block.get("category") not in EXCLUDED_CATEGORIES
    ]
    drafts = []
    current_title = ""
    current_section = ""
    current_parts = []
    current_source_blocks = []

    def finish():
        nonlocal current_title, current_parts, current_source_blocks
        if current_parts and current_title:
            draft = _draft_from_blocks(
                current_title,
                current_parts,
                current_source_blocks,
                current_section,
            )
            if draft:
                draft["draft_id"] = f"object_{len(drafts) + 1}"
                drafts.append(draft)
        current_title = ""
        current_parts = []
        current_source_blocks = []

    for block in lesson_blocks:
        text = block["text"].strip()

        if _is_heading(block):
            finish()
            current_title = text
            current_section = text
            current_source_blocks = [block]
            continue

        if not current_title:
            current_title = "Lesson text"
            current_section = ""
        current_parts.append({
            "type": "text",
            "text": text,
            "docling_label": block.get("docling_label", ""),
        })
        current_source_blocks.append(block)

    finish()
    drafts.extend(_visual_drafts(visuals, len(drafts) + 1))
    return drafts
