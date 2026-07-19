from __future__ import annotations

import re
import os
import textwrap

import fitz

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode

from .instructional_content_classifier import (
    classify_instructional_blocks,
    extract_pdf_text_blocks,
    split_classified_blocks,
)
from .llm_client import extract_json_from_text, get_llm_client


def extract_pdf_text(file_path: str) -> str:
    document = fitz.open(file_path)
    try:
        return "\n".join(page.get_text("text") for page in document)
    finally:
        document.close()


def clean_pdf_text_for_llm(text: str, limit: int | None = 12000) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""

    line_counts = {}
    for line in lines:
        key = line.casefold()
        line_counts[key] = line_counts.get(key, 0) + 1

    cleaned = []
    total_lines = len(lines)
    for index, line in enumerate(lines):
        lowered = line.lower()
        if re.fullmatch(r"(?:page\s*)?\d+(?:\s*/\s*\d+)?", lowered):
            continue
        if len(line) <= 120 and line_counts.get(line.casefold(), 0) >= 3:
            continue
        if index > total_lines * 0.65 and lowered in {"references", "bibliography", "appendix", "appendices"}:
            break
        if lowered.startswith(("http://", "https://", "www.")):
            continue
        cleaned.append(line)

    cleaned_text = "\n".join(cleaned)
    return cleaned_text[:limit] if limit else cleaned_text


def _split_preserved_paragraphs(cleaned_text: str) -> list[str]:
    paragraphs = []
    current = []
    for line in cleaned_text.splitlines():
        line = line.strip()
        if not line:
            if current:
                paragraphs.append(" ".join(current).strip())
                current = []
            continue
        current.append(line)
        if re.search(r"[.!?)]$", line):
            paragraphs.append(" ".join(current).strip())
            current = []
    if current:
        paragraphs.append(" ".join(current).strip())
    return [paragraph for paragraph in paragraphs if paragraph]


def build_teacher_text_narration(cleaned_text: str) -> list[dict]:
    narration_items = []
    for index, paragraph in enumerate(_split_preserved_paragraphs(cleaned_text), start=1):
        narration_items.append(
            {
                "order": index,
                "type": "teacher_text",
                "page": None,
                "section_title": "",
                "content": paragraph,
                "source": "pdf_exact_text",
            }
        )
    return narration_items


def build_fallback_learning_objects_from_text(cleaned_text: str) -> list[dict]:
    learning_objects = []
    for paragraph in _split_preserved_paragraphs(cleaned_text):
        learning_objects.append(
            {
                "order": len(learning_objects),
                "title": _title_from_teacher_text(paragraph, f"Lesson Content {len(learning_objects) + 1}"),
                "type": "lesson_content",
                "content": paragraph,
                "source": "pdf_exact_text",
                "source_page": None,
                "source_block_id": None,
                "source_excerpt": paragraph,
            }
        )
    return learning_objects


def extract_meaningful_pdf_images(file_path: str, max_images: int | None = None) -> list[dict]:
    images = []
    max_images = max_images or int(os.getenv("MAX_PDF_IMAGES_FOR_VISION", "4"))
    document = fitz.open(file_path)
    try:
        for page_index, page in enumerate(document, start=1):
            page_rect = page.rect
            page_area = max(page_rect.width * page_rect.height, 1)
            page_dict = page.get_text("dict")
            for block_index, block in enumerate(page_dict.get("blocks", [])):
                if block.get("type") != 1:
                    continue

                bbox = block.get("bbox") or [0, 0, 0, 0]
                width = max(float(bbox[2]) - float(bbox[0]), 0)
                height = max(float(bbox[3]) - float(bbox[1]), 0)
                image_area = width * height
                image_bytes = block.get("image")
                if not image_bytes:
                    continue
                if width < 48 or height < 48:
                    continue
                if image_area < 3000:
                    continue
                if image_area / page_area > 0.95:
                    continue
                if width / max(height, 1) > 12 or height / max(width, 1) > 12:
                    continue

                images.append(
                    {
                        "page_number": page_index,
                        "index": len(images),
                        "block_index": block_index,
                        "width": int(width),
                        "height": int(height),
                        "area": int(image_area),
                        "extension": block.get("ext") or "png",
                        "image_bytes": image_bytes,
                    }
                )
    finally:
        document.close()
    selected = sorted(images, key=lambda item: item["area"], reverse=True)[:max_images]
    for index, image in enumerate(selected):
        image["index"] = index
    return selected


def _limited_text(text: str, limit: int = 12000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit]


def _tokenize(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2 and token not in {"the", "and", "for", "with", "that", "this", "from"}
    }


def _choose_outline_node_with_llm(course: CourseGroup, title: str, text: str) -> OutlineNode | None:
    nodes = list(course.nodes.all().order_by("depth", "order", "id"))
    if not nodes:
        return None

    node_options = "\n".join(
        f"- id={node.id}; title={node.title}; depth={node.depth}"
        for node in nodes
    )
    prompt = textwrap.dedent(
        f"""
        Match this teacher-uploaded learning material to exactly one course topic.

        COURSE TOPICS:
        {node_options}

        MATERIAL_TITLE:
        {title}

        MATERIAL_TEXT:
        {_limited_text(text, 8000)}

        OUTPUT JSON ONLY:
        {{
          "outline_node_id": 123,
          "reason": "short reason"
        }}

        RULES:
        - Choose the most specific matching topic or subtopic.
        - Return only an id that appears in COURSE TOPICS.
        - If multiple topics match, prefer the deeper/more specific topic.
        """
    )
    response = get_llm_client().generate_text(prompt, max_tokens=400, timeout=240)
    output = response.get("text") if isinstance(response, dict) else None
    data = extract_json_from_text(output) if output else response
    if not isinstance(data, dict):
        return None

    try:
        node_id = int(data.get("outline_node_id"))
    except (TypeError, ValueError):
        return None

    return next((node for node in nodes if node.id == node_id), None)


def _choose_outline_node_by_keywords(course: CourseGroup, title: str, text: str) -> OutlineNode | None:
    text_tokens = _tokenize(f"{title} {text}")
    best_node = None
    best_score = 0.0

    for node in course.nodes.all().order_by("depth", "order", "id"):
        title_tokens = _tokenize(node.title)
        if not title_tokens:
            continue

        overlap = len(title_tokens & text_tokens)
        phrase_bonus = 2.0 if node.title.lower() in text.lower() or node.title.lower() in title.lower() else 0.0
        depth_bonus = node.depth * 0.2
        score = overlap + phrase_bonus + depth_bonus

        if score > best_score:
            best_score = score
            best_node = node

    return best_node if best_score > 0 else None


def choose_outline_node_for_material(course: CourseGroup, title: str, text: str) -> OutlineNode | None:
    return _choose_outline_node_with_llm(course, title, text) or _choose_outline_node_by_keywords(course, title, text)


def generate_lesson_metadata_from_text(text: str) -> dict:
    prompt = textwrap.dedent(
        """
        You are organizing teacher-provided PDF lesson text.

        TASK:
        Create metadata only. Do not rewrite, paraphrase, simplify, summarize, or generate lesson body content.

        OUTPUT JSON ONLY:
        {
          "lesson_title": "Short lesson title",
          "concepts": [
            {
              "canonical_title": "Concept title",
              "description": "Short concept metadata description",
              "source_excerpt": "Exact copied excerpt from the PDF text",
              "confidence": 0.9
            }
          ]
        }

        RULES:
        - The lesson_title may be normalized from the PDF title or heading.
        - source_excerpt must be copied exactly from the PDF_TEXT.
        - Do not create narration or learning object content.
        - Return valid JSON only.

        PDF_TEXT:
        """
    ) + _limited_text(text)

    client = get_llm_client()
    response = client.generate_text(prompt, max_tokens=1800, timeout=300)
    output = response.get("text") if isinstance(response, dict) else None
    data = extract_json_from_text(output) if output else response
    if not isinstance(data, dict):
        raise ValueError("The LLM did not return valid lesson metadata.")
    data["llm_metadata"] = response.get("llm_metadata") or client.metadata
    return data


def describe_pdf_images(images: list[dict], lesson_title: str = "", nearby_text: str = "") -> list[dict]:
    client = get_llm_client()
    descriptions = []
    for image in images:
        prompt = textwrap.dedent(
            f"""
            Describe this lesson image for a blind or visually impaired elementary learner.

            LESSON_TITLE:
            {lesson_title}

            PAGE_NUMBER:
            {image["page_number"]}

            IMAGE_INDEX:
            {image["index"] + 1}

            NEARBY_TEACHER_TEXT:
            {nearby_text[:1200]}

            OUTPUT JSON ONLY:
            {{
              "description": "Clear accessibility description",
              "educational_purpose": "How this image supports the lesson",
              "contains_text": false,
              "visible_text": ""
            }}

            RULES:
            - Use simple, age-appropriate language.
            - If this is a chart, diagram, graph, or table image, describe the axes, labels, groups, trend, and key comparisons.
            - Explain important objects, parts, positions, comparisons, sequence, and relationships.
            - Explain why the image matters to the lesson.
            - Avoid vague phrases like "as you can see".
            - Do not rely only on colors.
            - Do not invent details not visible in the image.
            - If the image contains readable text, include it in visible_text.
            """
        ).strip()
        response = client.describe_image(image["image_bytes"], prompt, max_tokens=400, timeout=300)
        output = response.get("text") if isinstance(response, dict) else None
        data = extract_json_from_text(output) if output else None
        if not isinstance(data, dict):
            data = {
                "description": output or "No local vision description was generated.",
                "educational_purpose": "",
                "contains_text": False,
                "visible_text": "",
            }

        descriptions.append(
            {
                "page": image["page_number"],
                "image_index": image["index"],
                "page_number": image["page_number"],
                "index": image["index"],
                "width": image["width"],
                "height": image["height"],
                "extension": image["extension"],
                "description": data.get("description", ""),
                "content": data.get("description", ""),
                "educational_purpose": data.get("educational_purpose", ""),
                "contains_text": bool(data.get("contains_text")),
                "visible_text": data.get("visible_text", ""),
                "source": "local_vision_model",
                "llm_metadata": response.get("llm_metadata") if isinstance(response, dict) else client.metadata,
            }
        )
    return descriptions


def _title_from_teacher_text(content: str, fallback: str) -> str:
    first_sentence = re.split(r"(?<=[.!?])\s+", content.strip())[0]
    return (first_sentence[:80].strip(" .") or fallback)[:255]


def _section_heading_title(text: str) -> str | None:
    match = re.fullmatch(r"\s*\d+(?:\.\d+)*\.?\s+(.+?)\s*", text or "")
    if not match:
        return None
    title = match.group(1).strip(" .")
    return title[:255] if title else None


def _looks_like_plain_subtopic_heading(text: str) -> str | None:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text or len(text) > 90 or re.search(r"[.!]$", text):
        return None
    words = text.split()
    word_count = len(words)
    title_words = [
        word.strip(":-,()")
        for word in words
        if word.lower().strip(":-,()") not in {"and", "or", "of", "in", "to", "for", "the", "a", "an"}
    ]
    title_cased_count = sum(1 for word in title_words if word[:1].isupper())
    title_like = text.endswith("?") or title_cased_count >= max(2, len(title_words) - 1)
    if 2 <= word_count <= 9 and re.search(r"[A-Za-z]", text) and title_like:
        return text.strip(" .")[:255]
    return None


def _raw_learning_object_heading_title(block: dict) -> str | None:
    if block.get("teacher_override"):
        return None
    text = block.get("text", "")
    return _section_heading_title(text) or _looks_like_plain_subtopic_heading(text)


def _learning_object_heading_title(block: dict) -> str | None:
    if block.get("teacher_override"):
        return None
    numbered_title = _section_heading_title(block.get("text", ""))
    if numbered_title:
        return numbered_title
    if block.get("category") != "lesson_content" or not block.get("include_in_narration"):
        return None
    return _looks_like_plain_subtopic_heading(block.get("text", ""))


def _heading_only_allowed(block: dict) -> bool:
    text = block.get("text", "") or ""
    return bool(re.fullmatch(r"\s*\d+\.\d+(?:\.\d+)*\.?\s+.+", text))


def _finalize_current_learning_object(current: dict | None, learning_objects: list[dict]) -> None:
    if not current:
        return
    parts = current.pop("parts", [])
    content = _format_section_content(parts)
    if content:
        current["content"] = content
        learning_objects.append(current)


def _is_instructional_table_or_chart_block(block: dict) -> bool:
    text = (block.get("text") or "").strip()
    if block.get("category") not in {"lesson_content", "table_header", "concept_metadata"}:
        return False
    return int(block.get("line_count") or 1) >= 3 and len(text.split()) >= 8


def _is_assessment_statement_content(block: dict) -> bool:
    if block.get("category") not in {"assessment", "reference", "document_metadata"}:
        return False
    text = re.sub(r"^[•\-\*\u2022]\s*", "", block.get("text", "") or "").strip()
    if not text or text.endswith("?"):
        return False
    return len(text.split()) >= 5 and bool(re.search(r"[.!;:]$", text))


def _block_is_kept_content(block: dict) -> bool:
    return (
        block.get("category") == "lesson_content"
        and block.get("include_in_narration")
        and not _raw_learning_object_heading_title(block)
    ) or _is_instructional_table_or_chart_block(block) or _is_assessment_statement_content(block)


def _heading_has_following_content(blocks: list[dict], start_index: int) -> bool:
    for next_block in blocks[start_index + 1 :]:
        text = (next_block.get("text") or "").strip()
        if not text or _is_image_caption(text):
            continue
        if _block_is_kept_content(next_block):
            return True
        if _raw_learning_object_heading_title(next_block) or next_block.get("category") in {
            "learning_objective",
            "assessment",
            "teacher_note",
            "reference",
        }:
            return False
    return False


def _image_caption_title(text: str) -> str | None:
    match = re.match(r"\s*Figure\s+\d+\.\s*(.+)", text or "", flags=re.IGNORECASE)
    if not match:
        return None
    caption = match.group(1).strip()
    caption = re.split(r"\bThis image\b|\bThis figure\b", caption, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    caption = caption.rstrip(". ")
    return caption[:255] if caption else None


def _is_image_caption(text: str) -> bool:
    return bool(re.match(r"\s*Figure\s+\d+\.", text or "", flags=re.IGNORECASE))


def _format_section_content(parts: list[str]) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip()).strip()


def build_section_learning_objects(classified_blocks: list[dict], image_descriptions: list[dict]) -> list[dict]:
    learning_objects = []
    has_section_headings = any(_learning_object_heading_title(block) for block in classified_blocks)
    figure_titles = [
        _image_caption_title(block.get("text", ""))
        for block in classified_blocks
        if _image_caption_title(block.get("text", ""))
    ]

    for index, image in enumerate(image_descriptions):
        title = (
            image.get("title")
            or (figure_titles[index] if index < len(figure_titles) else None)
            or f"Image description {index + 1}"
        )
        learning_objects.append(
            {
                "order": len(learning_objects),
                "title": title,
                "type": "image_description",
                "content": image.get("description", ""),
                "source": "local_vision_model",
                "source_page": image.get("page_number") or image.get("page"),
                "source_block_id": None,
                "image_index": image.get("index", index),
                "source_excerpt": image.get("description", ""),
            }
        )

    current = None
    for index, block in enumerate(classified_blocks):
        text = block.get("text", "").strip()
        if not text or _is_image_caption(text):
            continue

        heading_title = _learning_object_heading_title(block)
        if not heading_title:
            raw_heading_title = _raw_learning_object_heading_title(block)
            if raw_heading_title and _heading_has_following_content(classified_blocks, index):
                heading_title = raw_heading_title
        if heading_title:
            _finalize_current_learning_object(current, learning_objects)
            current = {
                "order": len(learning_objects),
                "title": heading_title,
                "type": "lesson_content",
                "content": "",
                "source": "teacher_pdf",
                "source_page": block.get("page"),
                "source_block_id": block.get("block_id"),
                "source_excerpt": "",
                "parts": [],
            }
            continue

        keep_as_content = _block_is_kept_content(block)

        if not keep_as_content:
            if block.get("category") in {"assessment", "teacher_note", "concept_metadata", "table_header", "reference"}:
                _finalize_current_learning_object(current, learning_objects)
                current = None
            continue

        if current is None:
            if has_section_headings:
                continue
            learning_objects.append(
                {
                    "order": len(learning_objects),
                    "title": _title_from_teacher_text(text, f"Lesson Content {len(learning_objects) + 1}"),
                    "type": "lesson_content",
                    "content": text,
                    "source": "teacher_pdf",
                    "source_page": block.get("page"),
                    "source_block_id": block.get("block_id"),
                    "source_excerpt": text,
                }
            )
            continue
        if current.get("title", "").casefold() == text.casefold():
            continue
        current["parts"].append(text)
        if not current.get("source_excerpt"):
            current["source_excerpt"] = text

    _finalize_current_learning_object(current, learning_objects)

    for order, item in enumerate(learning_objects):
        item.pop("heading_only_allowed", None)
        item["order"] = order
    return learning_objects


def build_narration_script_from_learning_objects(learning_objects: list[dict]) -> list[dict]:
    narration = []
    for item in learning_objects:
        narration.append(
            {
                "order": len(narration) + 1,
                "type": item.get("type"),
                "title": item.get("title", ""),
                "page": item.get("source_page"),
                "content": item.get("content", ""),
                "source": item.get("source"),
                "source_block_id": item.get("source_block_id"),
                "image_index": item.get("image_index"),
            }
        )
    return narration


def build_narration_script(teacher_items: list[dict], image_descriptions: list[dict]) -> list[dict]:
    narration = []
    order = 1
    for item in teacher_items:
        item = {**item, "order": order}
        narration.append(item)
        order += 1
    for image in image_descriptions:
        narration.append(
            {
                "order": order,
                "type": "image_description",
                "page": image.get("page_number"),
                "image_index": image.get("index"),
                "content": image.get("description", ""),
                "source": "local_vision_model",
                "placement": "inferred_after_teacher_text",
            }
        )
        order += 1
    return narration


def build_narration_script_from_classified(classified_blocks: list[dict], image_descriptions: list[dict]) -> list[dict]:
    narration = []
    order = 1
    for block in classified_blocks:
        if not block.get("include_in_narration"):
            continue
        narration.append(
            {
                "order": order,
                "type": block.get("category") or "lesson_content",
                "page": block.get("page"),
                "content": block.get("text", ""),
                "source": "teacher_pdf",
                "source_block_id": block.get("block_id"),
                "category": block.get("category"),
            }
        )
        order += 1

    for image in image_descriptions:
        narration.append(
            {
                "order": order,
                "type": "image_description",
                "page": image.get("page_number"),
                "image_index": image.get("index"),
                "content": image.get("description", ""),
                "source": "local_vision_model",
                "placement": "inferred_after_lesson_content",
            }
        )
        order += 1
    return narration


def build_learning_objects_from_narration(narration_script: list[dict]) -> list[dict]:
    learning_objects = []
    for item in narration_script:
        if item.get("type") not in {"teacher_text", "lesson_content"}:
            continue
        source = "teacher_pdf" if item.get("type") == "lesson_content" else "pdf_exact_text"
        learning_objects.append(
            {
                "order": len(learning_objects),
                "title": _title_from_teacher_text(item.get("content", ""), f"Teacher Text {len(learning_objects) + 1}"),
                "type": item.get("type"),
                "content": item.get("content", ""),
                "source": source,
                "source_page": item.get("page"),
                "source_block_id": item.get("source_block_id"),
                "source_excerpt": item.get("content", ""),
                "narration_item_order": item.get("order"),
            }
        )
    return learning_objects


def build_lesson_playlist(narration_script: list[dict]) -> list[dict]:
    playlist = []
    for item in narration_script:
        playlist.append(
            {
                "order": len(playlist),
                "title": (
                    item.get("title")
                    or (
                        _title_from_teacher_text(item.get("content", ""), f"Teacher Text {item.get('order')}")
                        if item.get("type") in {"teacher_text", "lesson_content"}
                        else f"Image description {item.get('image_index', 0) + 1}"
                    )
                ),
                "type": item.get("type"),
                "narration_item_order": item.get("order"),
            }
        )
    return playlist


def _review_block(block: dict) -> dict:
    return {
        "block_id": block.get("block_id"),
        "page": block.get("page"),
        "content": block.get("text", ""),
        "text": block.get("text", ""),
        "category": block.get("category"),
        "include_in_narration": bool(block.get("include_in_narration")),
        "confidence": block.get("confidence"),
        "reason": block.get("reason", ""),
        "source": "teacher_pdf",
    }


def _review_blocks(blocks: list[dict]) -> list[dict]:
    return [_review_block(block) for block in blocks]


def review_learning_objects_for_bvi_learners(learning_objects: list[dict]) -> list[dict]:
    candidates = [
        {
            "index": index,
            "title": item.get("title", ""),
            "content": _limited_text(item.get("content", ""), 1200),
            "source": item.get("source", ""),
        }
        for index, item in enumerate(learning_objects)
        if item.get("type") != "image_description"
    ]
    if not candidates:
        return learning_objects

    prompt = textwrap.dedent(
        f"""
        Review candidate learning objects for an audio lesson for blind or visually impaired learners.

        You are not making notes from the PDF.
        You are not extracting key ideas.
        You are not writing or improving a script.
        The teacher's exact PDF text is what students will hear.
        Your only job is to decide whether each existing candidate should be kept or dropped.

        Keep only objects that teach learner-facing knowledge, explanations, examples, procedures, or guided practice.
        Drop objects that are page labels, document titles, metadata, objectives lists, prerequisite notes,
        table headers without standalone teaching value, duplicate labels, references, or teacher/admin notes.

        Return JSON only:
        {{
          "items": [
            {{"index": 0, "keep": true, "reason": "short reason"}}
          ]
        }}

        Do not return rewritten titles or rewritten content. Only return index, keep, and reason.

        CANDIDATES:
        {json_dumps_for_prompt(candidates)}
        """
    ).strip()

    try:
        response = get_llm_client().generate_text(prompt, max_tokens=1800, timeout=240)
        output = response.get("text") if isinstance(response, dict) else None
        data = extract_json_from_text(output) if output else response
    except Exception:
        return learning_objects

    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return learning_objects

    keep_by_index = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            keep_by_index[int(item.get("index"))] = item.get("keep") is True
        except (TypeError, ValueError):
            continue
    if not keep_by_index:
        return learning_objects

    reviewed = []
    for index, item in enumerate(learning_objects):
        if item.get("type") == "image_description" or keep_by_index.get(index, True):
            reviewed.append(item)
    for order, item in enumerate(reviewed):
        item["order"] = order
    return reviewed


def json_dumps_for_prompt(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)


def _sync_learning_objects(material: LearningMaterial, generated_json: dict):
    material.learning_objects.all().delete()
    for index, item in enumerate(generated_json.get("learning_objects", [])):
        LearningObject.objects.create(
            material=material,
            kind=LearningObject.Kind.TEXT,
            title=(item.get("title") or f"Learning Object {index + 1}")[:255],
            content=item.get("content") or "",
            order=index,
        )


def rebuild_generated_outputs_from_classifications(generated_json: dict) -> dict:
    classified_blocks = generated_json.get("classified_blocks") or []
    image_descriptions = generated_json.get("image_descriptions") or []
    sections = split_classified_blocks(classified_blocks)
    learning_objects = build_section_learning_objects(classified_blocks, image_descriptions)
    narration_script = build_narration_script_from_learning_objects(learning_objects)
    playlist = build_lesson_playlist(narration_script)
    updated = {
        **generated_json,
        "narration_script": narration_script,
        "learning_objects": learning_objects,
        "learning_objectives": _review_blocks(sections["learning_objectives"]),
        "assessments": _review_blocks(sections["assessments"]),
        "teacher_notes": _review_blocks(sections["teacher_notes"]),
        "concept_metadata_blocks": _review_blocks(sections["concept_metadata_blocks"]),
        "ignored_blocks": _review_blocks(sections["ignored_blocks"]),
        "lesson_playlist": playlist,
    }
    return updated


def apply_classification_override(
    material: LearningMaterial,
    block_id: int,
    category: str | None = None,
    include_in_narration: bool | None = None,
) -> LearningMaterial:
    generated_json = material.generated_json or {}
    classified_blocks = generated_json.get("classified_blocks") or []
    changed = False
    for block in classified_blocks:
        if int(block.get("block_id") or -1) != int(block_id):
            continue
        if category:
            block["category"] = category
        if include_in_narration is not None:
            block["include_in_narration"] = bool(include_in_narration)
        elif category:
            block["include_in_narration"] = category == "lesson_content"
        block["teacher_override"] = True
        block["reason"] = "Teacher override."
        changed = True
        break
    if not changed:
        raise ValueError("Classified block was not found.")

    material.generated_json = rebuild_generated_outputs_from_classifications(generated_json)
    material.save(update_fields=["generated_json"])
    _sync_learning_objects(material, material.generated_json)
    return material


def generate_material_outputs(material: LearningMaterial) -> LearningMaterial:
    try:
        text = extract_pdf_text(material.pdf_file.path)
        if not text.strip():
            raise ValueError("No readable text was found in the PDF.")
        extracted_blocks = extract_pdf_text_blocks(material.pdf_file.path)
        cleaned_preserved_text = clean_pdf_text_for_llm(text, limit=None)
        if not cleaned_preserved_text.strip():
            raise ValueError("No meaningful lesson text was found in the PDF.")
        metadata_text = _limited_text(cleaned_preserved_text)

        images = extract_meaningful_pdf_images(material.pdf_file.path)

        if material.outline_node_id is None:
            matched_node = choose_outline_node_for_material(material.course, material.title, metadata_text)
            if matched_node:
                material.outline_node = matched_node

        metadata = generate_lesson_metadata_from_text(metadata_text)
        lesson_title = metadata.get("lesson_title") or material.title
        classified_blocks = classify_instructional_blocks(extracted_blocks)
        image_descriptions = describe_pdf_images(images, lesson_title, cleaned_preserved_text)
        sections = split_classified_blocks(classified_blocks)
        learning_objects = build_section_learning_objects(classified_blocks, image_descriptions)
        fallback_used = False
        has_lesson_content = any(
            item.get("type") in {"teacher_text", "lesson_content"} and item.get("content", "").strip()
            for item in learning_objects
        )
        if not has_lesson_content:
            image_objects = [item for item in learning_objects if item.get("type") == "image_description"]
            fallback_objects = build_fallback_learning_objects_from_text(cleaned_preserved_text)
            learning_objects = image_objects + fallback_objects
            for order, item in enumerate(learning_objects):
                item["order"] = order
            fallback_used = bool(fallback_objects)
        learning_objects = review_learning_objects_for_bvi_learners(learning_objects)
        narration_script = build_narration_script_from_learning_objects(learning_objects)
        playlist = build_lesson_playlist(narration_script)
        generated_json = {
            "generated_json_version": 3,
            "lesson_title": lesson_title,
            "summary": "",
            "original_text": text,
            "cleaned_preserved_text": cleaned_preserved_text,
            "classified_blocks": classified_blocks,
            "narration_script": narration_script,
            "learning_objects": learning_objects,
            "learning_objectives": _review_blocks(sections["learning_objectives"]),
            "assessments": _review_blocks(sections["assessments"]),
            "teacher_notes": _review_blocks(sections["teacher_notes"]),
            "concept_metadata_blocks": _review_blocks(sections["concept_metadata_blocks"]),
            "ignored_blocks": _review_blocks(sections["ignored_blocks"]),
            "concepts": metadata.get("concepts", []),
            "image_descriptions": image_descriptions,
            "lesson_playlist": playlist,
            "llm_metadata": metadata.get("llm_metadata") or get_llm_client().metadata,
            "preservation_metadata": {
                "teacher_text_preserved": True,
                "teacher_text_rewritten_by_llm": False,
                "learning_object_fallback_used": fallback_used,
                "cleanup_applied": [
                    "removed_repeated_header_footer",
                    "removed_page_number",
                    "normalized_excess_whitespace",
                    "joined_layout_broken_lines",
                ],
            },
        }

        if generated_json["lesson_title"]:
            material.title = str(generated_json["lesson_title"])[:255]
        material.extracted_text = text
        material.generated_json = generated_json
        material.status = LearningMaterial.Status.COMPLETED
        material.error_message = ""
        material.save(update_fields=["outline_node", "title", "extracted_text", "generated_json", "status", "error_message"])

        _sync_learning_objects(material, generated_json)
    except Exception as exc:
        material.status = LearningMaterial.Status.FAILED
        material.error_message = str(exc)
        material.save(update_fields=["status", "error_message"])

    return material
