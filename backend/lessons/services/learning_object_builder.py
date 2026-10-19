import re
from statistics import median


LIST_ITEM_START = re.compile(
    r"^\s*(?:[-*\u2022\u2023\u25E6\u2043\u2219\u25AA\u25CF]\s+|"
    r"\d+[.)]\s+|\(\d+\)\s+|[A-Za-z][.)]\s+|\([A-Za-z]\)\s+)"
)


def join_part_text(parts):
    """Combine PDF blocks without flattening separate list items."""
    result = ""
    for part in parts:
        if part.get("type") != "text":
            continue
        text = (part.get("text") or "").strip()
        if not text:
            continue
        separator = "\n" if result and LIST_ITEM_START.match(text) else " "
        result = f"{result}{separator}{text}" if result else text
    return result


def find_body_font_size(blocks):
    sizes = [
        block["dominant_font_size"]
        for block in blocks
        if block.get("dominant_font_size") is not None
        and not block.get("is_bold")
        and len(block["text"].split()) >= 4
    ]
    return median(sizes) if sizes else None


def is_heading(block, body_font_size):
    if body_font_size is None:
        return False

    font_size = block.get("dominant_font_size")
    if font_size is None:
        return False

    return (
        block.get("is_bold", False)
        and font_size >= body_font_size + 1
        and len(block["text"].split()) <= 10
    )


def parse_numbered_concept(text, leading_bold_text=None):
    if leading_bold_text:
        bold_match = re.fullmatch(r"\d+[.)]\s+(.+)", leading_bold_text)

        if bold_match and text.startswith(leading_bold_text):
            title = bold_match.group(1)
            first_sentence = text[len(leading_bold_text):].strip()
            return title, first_sentence

    match = re.match(r"^\d+[.)]\s+(.+?)\s*[—–-]\s+(.+)$", text)

    if match is None:
        return None

    title = match.group(1)
    first_sentence = match.group(2)
    return title, first_sentence


def continues_table(previous, current):
    """Keep aligned table fragments at a page break in one visual object."""
    if current["page"] != previous["page"] + 1:
        return False
    if not previous.get("page_height") or not current.get("page_height"):
        return False
    if previous["page_height"] - previous["bbox"][3] > previous["page_height"] * 0.12:
        return False
    if current["bbox"][1] > current["page_height"] * 0.12:
        return False
    if (
        previous.get("column_count") is not None
        and current.get("column_count") is not None
        and previous["column_count"] != current["column_count"]
    ):
        return False
    width = min(
        previous["bbox"][2] - previous["bbox"][0],
        current["bbox"][2] - current["bbox"][0],
    )
    return (
        abs(previous["bbox"][0] - current["bbox"][0]) <= width * 0.05
        and abs(previous["bbox"][2] - current["bbox"][2]) <= width * 0.05
    )


def build_learning_objects(blocks, visuals=()):
    current_section = ""
    section_font_size = 0
    learning_objects = []
    body_font_size = find_body_font_size(blocks)
    current_object = None
    skipping_section = False
    first_page = blocks[0]["page"] if blocks else None

    is_cover_page = first_page is not None and any(
        block.get("page") == first_page
        and block["text"].strip().rstrip(":").casefold()
        in {"contents", "table of contents", "in this guide"}
        for block in blocks
    )

    def finish_current():
        nonlocal current_object
        if current_object is not None and current_object["parts"]:
            current_object.setdefault(
                "section_title", current_section
            )
            current_object["draft_id"] = f"object_{len(learning_objects) + 1}"
            current_object["content"] = join_part_text(current_object["parts"])
            learning_objects.append(current_object)
        current_object = None

    # Table/figure text is already visible in its crop. Do not repeat it as
    # paragraph text beneath the image.
    visual_regions = [
        visual for visual in visuals
        if visual.get("type") in {"table", "figure"}
    ]

    def inside_region(block, visual):
        x0, y0, x1, y1 = block["bbox"]
        center_x = (x0 + x1) / 2
        center_y = (y0 + y1) / 2
        return (
            block["page"] == visual["page"]
            and visual["bbox"][0] <= center_x <= visual["bbox"][2]
            and visual["bbox"][1] <= center_y <= visual["bbox"][3]
        )

    def inside_visual(block):
        return any(inside_region(block, visual) for visual in visual_regions)

    def figure_section(visual):
        # Preserve an authored heading near the top of the figure crop.
        top = visual["bbox"][1]
        heading_limit = top + (visual["bbox"][3] - top) * 0.35
        headings = [
            block for block in blocks
            if inside_region(block, visual)
            and block["bbox"][1] <= heading_limit
            and is_heading(block, body_font_size)
            and block.get("category") != "document_metadata"
            and not block.get("starts_excluded_section")
        ]
        if not headings:
            return current_section
        heading = min(headings, key=lambda block: (
            -(block.get("dominant_font_size") or 0), block["bbox"][1],
        ))
        numbered = parse_numbered_concept(heading["text"], heading.get("leading_bold_text"))
        return numbered[0] if numbered else heading["text"].strip()

    # Combine text and visual blocks before grouping them into objects.
    items = [("text", block) for block in blocks if not inside_visual(block)]
    items.extend(("visual", visual) for visual in visuals)
    items.sort(key=lambda item: (
        item[1]["page"],
        item[1]["bbox"][1],
        item[1]["bbox"][0],
    ))

    for kind, item in items:
        if is_cover_page and item.get("page") == first_page:
            continue

        if kind == "visual":
            if skipping_section:
                # A captioned figure after objectives can still teach a
                # concept. Keep it as a visual draft until the next heading.
                if item.get("type") == "figure" and item.get("caption"):
                    finish_current()
                    current_object = {
                        "type": "image",
                        "title": f"Figure on page {item['page']}",
                        "section_title": figure_section(item),
                        "parts": [
                            {
                                "type": "image",
                                "image_url": item["image_url"],
                                "page": item["page"],
                                "bbox": item["bbox"],
                                "source_block_index": item.get("source_block_index"),
                            },
                            {"type": "text", "text": item["caption"]},
                        ],
                        "source_block_ids": [],
                        "source_page": item["page"],
                        "source_block_index": item.get("source_block_index"),
                        "needs_review": True,
                    }
                continue

            visual_type = (
                "table" if item.get("type") == "table" else "image"
            )
            visual_part = {
                "type": visual_type,
                "image_url": item["image_url"],
                "page": item["page"],
                "bbox": item["bbox"],
                "source_block_index": item.get("source_block_index"),
            }
            if visual_type == "table":
                visual_part["page_height"] = item.get("page_height")
                visual_part["column_count"] = item.get("column_count")
                table_text = " ".join(
                    block["text"] for block in blocks
                    if inside_region(block, item)
                )
                if (
                    current_object is not None
                    and current_object.get("type") == "table"
                    and continues_table(current_object["parts"][-1], visual_part)
                ):
                    current_object["parts"].append(visual_part)
                    current_object["source_pages"].append(item["page"])
                    current_object["source_text"] = " ".join(
                        part for part in (current_object["source_text"], table_text)
                        if part
                    )
                    continue

                related_object = (
                    current_object
                    if current_object is not None and not current_object.get("type")
                    else None
                )
                finish_current()
                current_object = {
                    "type": "table",
                    "title": (
                        f"Table for {related_object['title']}"
                        if related_object is not None
                        else f"Table on page {item['page']}"
                    ),
                    "parts": [visual_part],
                    "source_block_ids": [],
                    "source_page": item["page"],
                    "source_pages": [item["page"]],
                    "source_block_index": item.get("source_block_index"),
                    "source_text": table_text,
                    "related_object_id": (
                        related_object.get("draft_id") if related_object is not None else None
                    ),
                    "related_title": (
                        related_object["title"] if related_object is not None else None
                    ),
                    "needs_review": True,
                }
                continue

            finish_current()
            caption = (item.get("caption") or "").strip()
            current_object = {
                "type": "image",
                "title": f"Image on page {item['page']}",
                "section_title": figure_section(item),
                "parts": [visual_part],
                "source_text": caption,
                "source_block_ids": [],
                "source_page": item["page"],
                "source_block_index": item.get("source_block_index"),
                "needs_review": True,
            }
            if caption:
                current_object["parts"].append({
                    "type": "text", "text": caption,
                })
            continue

        block = item
        text = block["text"]

        if (
            current_object is not None
            and current_object.get("type") == "image"
        ):
            image_part = current_object["parts"][0]
            is_caption = (
                block["page"] == image_part["page"]
                and -3 <= block["bbox"][1] - image_part["bbox"][3] <= 40
                and re.match(
                    r"^\s*(?:figure|fig\.?)\s*\d+\b",
                    text,
                    re.IGNORECASE,
                )
            )

            if is_caption:
                current_object["source_text"] = text
                current_object["source_block_ids"].append(
                    block["block_id"]
                )

                if not any(
                    part.get("text") == text
                    for part in current_object["parts"]
                ):
                    current_object["parts"].append({
                        "type": "text",
                        "text": text,
                    })

                continue

            finish_current()

        # Objectives, quizzes, etc. are not lesson objects.
        if block.get("starts_excluded_section"):
            finish_current()
            skipping_section = True
            current_section = ""
            section_font_size = 0
            continue

        if block.get("category") == "document_metadata":
            continue

        numbered_concept = parse_numbered_concept(
            text, block.get("leading_bold_text"),
        )
        starts_lesson = (
            is_heading(block, body_font_size)
            or (
                numbered_concept is not None
                and (not skipping_section or block.get("leading_bold_text"))
            )
        )
        if skipping_section and not starts_lesson:
            continue

        if starts_lesson:
            skipping_section = False
            finish_current()

            if numbered_concept is not None:
                title, first_sentence = numbered_concept
                parts = ([{"type": "text", "text": first_sentence}]
                         if first_sentence else [])
            else:
                title = text
                parts = []
            heading_size = block.get("dominant_font_size") or 0
            if (
                not current_section
                or numbered_concept is not None
                or heading_size >= section_font_size - 0.5
            ):
                current_section = title
                section_font_size = heading_size

            current_object = {
                "title": title,
                "parts": parts,
                "source_block_ids": [block["block_id"]],
            }

        elif (
            current_object is not None
            and current_object.get("type") == "table"
            and current_object.get("related_object_id")
        ):
            # Prose after a table still belongs to its lesson concept, not
            # to the visual object. Keep the table available as a separate
            # object for later topic matching and explanation.
            related = next(
                (
                    obj for obj in reversed(learning_objects)
                    if obj["draft_id"] == current_object["related_object_id"]
                ),
                None,
            )
            if related is not None:
                related["parts"].append({"type": "text", "text": text})
                related["source_block_ids"].append(block["block_id"])
                related["content"] = join_part_text(related["parts"])
            else:
                current_object["parts"].append({"type": "text", "text": text})
                current_object["source_block_ids"].append(block["block_id"])
        elif current_object is not None:
            current_object["parts"].append({"type": "text", "text": text})
            current_object["source_block_ids"].append(block["block_id"])
        elif block.get("category") == "lesson_content":
            # Preserve genuinely unheaded lesson prose, but do not turn a
            # cover title, subtitle or other short uncertain front matter
            # into a generic object before the first real section heading.
            current_object = {
                "type": "text",
                "title": "Lesson text",
                "parts": [{"type": "text", "text": text}],
                "source_block_ids": [block["block_id"]],
            }

    finish_current()

    return learning_objects
