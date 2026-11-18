"""Build ordered learning-object drafts from classified PDF blocks."""

HEADING_LABELS = {"title", "section_header", "heading"}
EXCLUDED_CATEGORIES = {"assessment", "other"}


def join_part_text(parts):
    """Keep source markers, indentation and separate block boundaries."""
    lines = []
    for part in parts:
        text = (part.get("text") or "").strip()
        if not text:
            continue
        if part.get("docling_label") == "list_item":
            marker = (part.get("list_marker") or "").strip()
            if marker and not (text == marker or text.startswith((marker + " ", marker + "\t"))):
                text = f"{marker} {text}"
            text = "  " * max(0, int(part.get("list_depth") or 0)) + text
        lines.append(text)

    return "\n".join(lines)


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
        "document_order": source_blocks[0].get("document_order"),
    }


def _visual_drafts(visuals, start_order):
    drafts = []
    for index, visual in enumerate(visuals or [], start=start_order):
        image_url = (visual.get("image_url") or "").strip()
        if not image_url:
            continue
        caption = (visual.get("caption") or "").strip()
        section_title = (visual.get("section_title") or "").strip()
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
            "title": section_title or caption or f"Image on page {visual.get('page', '')}".strip(),
            "section_title": section_title,
            "caption": caption,
            "content": caption,
            "source_text": caption,
            "parts": parts,
            "source_block_ids": [],
            "source_page": visual.get("page"),
            "document_order": visual.get("document_order"),
            "before_block_index": visual.get("before_block_index"),
        })
    return drafts
def _table_drafts(tables, start_order=1):
    drafts = []

    for index, table in enumerate(tables, start=start_order):
        caption = (table.get("caption") or "").strip()
        section_title = (table.get("section_title") or "").strip()
        image_url = (table.get("image_url") or "").strip()
        data = table["data"]

        parts = [{
            "type": "table",
            "image_url": image_url,
            "data": data,
            "page": table.get("page"),
        }]

        if caption:
            parts.append({"type": "text", "text": caption})

        drafts.append({
            "draft_id": f"table_{index}",
            "type": "table",
            "title": (
                section_title
                or caption
                or f"Table on page {table.get('page', '')}"
            ),
            "section_title": section_title,
            "caption": caption,
            "table_data": data,
            "content": caption,
            "source_text": caption,
            "parts": parts,
            "source_block_ids": [],
            "source_page": table.get("page"),
            "document_order": table.get("document_order"),
            "before_block_index": table.get("before_block_index"),
        })

    return drafts

def build_learning_objects(blocks, visuals=(), tables=()):
    """Group lesson-content blocks under their nearest authored heading."""
    caption_refs = {
        ref
        for visual in visuals
        if visual.get("image_url")
        for ref in visual.get("caption_refs", [])
    }

    caption_refs.update(
        ref
        for table in tables
        for ref in table.get("caption_refs", [])
    )

    lesson_blocks = [
        block for block in blocks
        if (block.get("text") or "").strip()
        and block.get("category") not in EXCLUDED_CATEGORIES
        and block.get("docling_ref") not in caption_refs
    ]
    ordered_items = [
        ("text", block)
        for block in lesson_blocks
    ]
    ordered_items.extend(
        ("image", visual)
        for visual in _visual_drafts(visuals, 1)
    )
    ordered_items.extend(
        ("table", table)
        for table in _table_drafts(tables)
    )
    ordered_items.sort(
        key=lambda entry: (
            entry[1]["document_order"]
            if entry[1].get("document_order") is not None
            else float("inf")
        )
    )
    drafts = []
    current_section = ""

    for item_type, block in ordered_items:
        if item_type in {"image", "table"}:
            drafts.append(block)
            current_section = (
                block.get("section_title") or current_section
            )
            continue

        text = block["text"].strip()

        if _is_heading(block):
            current_section = text
            continue

        draft = _draft_from_blocks(
            title=current_section or "Lesson text",
            parts=[{
                **block,
                "type": "text",
                "text": text,
            }],
            source_blocks=[block],
            section_title=current_section,
        )

        if draft:
            draft["draft_id"] = f"object_{len(drafts) + 1}"
            drafts.append(draft)

    return drafts