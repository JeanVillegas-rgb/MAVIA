from __future__ import annotations

import re
import os
import json
import textwrap
import logging

import fitz
from django.db import DatabaseError

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode

from .instructional_content_classifier import (
    classify_instructional_blocks,
    extract_pdf_text_blocks,
    split_classified_blocks,
)
from .llm_client import extract_json_from_text, get_llm_client

logger = logging.getLogger(__name__)


class MaterialDeletedDuringGeneration(RuntimeError):
    pass


def _format_json_for_prompt(value) -> str:
    if not value:
        return "Not provided"
    return json.dumps(value, ensure_ascii=False)


def _material_exists(material: LearningMaterial) -> bool:
    return bool(material.pk) and LearningMaterial.objects.filter(pk=material.pk).exists()


def _save_material_update(material: LearningMaterial, fields: list[str]):
    if not _material_exists(material):
        raise MaterialDeletedDuringGeneration(
            f"Learning material {material.pk} was deleted before generation finished."
        )
    try:
        material.save(update_fields=fields)
    except DatabaseError as exc:
        if not _material_exists(material):
            raise MaterialDeletedDuringGeneration(
                f"Learning material {material.pk} was deleted before generation finished."
            ) from exc
        raise


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


def _has_enough_embedded_pdf_text(text: str) -> bool:
    cleaned = clean_pdf_text_for_llm(text, limit=None)
    words = re.findall(r"[A-Za-z0-9]+", cleaned)
    return len(cleaned) >= 120 and len(words) >= 25


def _render_pdf_pages_as_images(file_path: str, max_pages: int | None = None) -> list[dict]:
    pages = []
    document = fitz.open(file_path)
    try:
        limit = max_pages or int(os.getenv("MAX_PDF_PAGE_IMAGES_FOR_VISION", "8"))
        matrix = fitz.Matrix(2, 2)
        for page_index, page in enumerate(document, start=1):
            if page_index > limit:
                break
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            pages.append(
                {
                    "page_number": page_index,
                    "image_bytes": pixmap.tobytes("png"),
                }
            )
    finally:
        document.close()
    return pages


def transcribe_image_only_pdf_pages(file_path: str) -> str:
    client = get_llm_client()
    transcribed_pages = []
    for page in _render_pdf_pages_as_images(file_path):
        prompt = textwrap.dedent(
            f"""
            Transcribe this teacher-provided PDF page.

            PAGE:
            {page["page_number"]}

            OUTPUT JSON ONLY:
            {{
              "page": {page["page_number"]},
              "text": "All visible instructional text from the page"
            }}

            RULES:
            - Copy all visible teacher content as accurately as possible.
            - Preserve headings, bullets, numbering, table rows, labels, and reading order.
            - Do not summarize, simplify, explain, or add new content.
            - If the page has no readable text, return an empty text string.
            """
        ).strip()
        response = client.describe_image(page["image_bytes"], prompt, max_tokens=1800, timeout=300)
        output = response.get("text") if isinstance(response, dict) else None
        data = extract_json_from_text(output) if output else None
        text = data.get("text") if isinstance(data, dict) else output
        if text and str(text).strip():
            transcribed_pages.append(f"Page {page['page_number']}\n{str(text).strip()}")
    return "\n\n".join(transcribed_pages).strip()


def _text_blocks_from_transcription(text: str) -> list[dict]:
    blocks = []
    block_id = 1
    page = None
    for chunk in re.split(r"\n\s*\n", text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        page_match = re.match(r"^Page\s+(\d+)\s*(.*)$", chunk, flags=re.IGNORECASE | re.DOTALL)
        if page_match:
            page = int(page_match.group(1))
            chunk = page_match.group(2).strip()
            if not chunk:
                continue
        for line in chunk.splitlines():
            clean_line = re.sub(r"\s+", " ", line).strip()
            if not clean_line:
                continue
            blocks.append(
                {
                    "block_id": block_id,
                    "page": page,
                    "block_index": block_id - 1,
                    "text": clean_line,
                    "line_count": 1,
                    "source": "vision_page_transcription",
                }
            )
            block_id += 1
    return blocks


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
        if _is_admin_or_system_support_text(paragraph):
            continue
        learning_objects.append(
            {
                "order": len(learning_objects),
                "title": "",
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


def _score_outline_node(node: OutlineNode, text_tokens: set[str], title: str, text: str) -> float:
    path_tokens = _tokenize(" ".join(item.title for item in _outline_path(node)))
    if not path_tokens:
        return 0.0
    overlap = len(path_tokens & text_tokens)
    phrase_bonus = 2.0 if node.title.lower() in text.lower() or node.title.lower() in title.lower() else 0.0
    depth_bonus = node.depth * 0.4
    return overlap + phrase_bonus + depth_bonus


def _choose_outline_node_with_llm(course: CourseGroup, title: str, text: str) -> OutlineNode | None:
    nodes = list(course.nodes.all().order_by("depth", "order", "id"))
    if not nodes:
        return None

    node_options = "\n".join(
        f"- id={node.id}; title={node.title}; depth={node.depth}; parent_title={node.parent.title if node.parent else 'ROOT'}"
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
        - If the material matches a subtopic, choose that subtopic even if the module also matches.
        - Do not choose a sibling topic unless the PDF text clearly matches that sibling.
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

    chosen = next((node for node in nodes if node.id == node_id), None)

    # Defensive check: ensure the LLM's chosen node shares tokens with the material
    # If not, fall back to keyword-based matching. Also log choices for debugging.
    try:
        text_tokens = _tokenize(f"{title} {text}")
        if chosen:
            chosen_score = _score_outline_node(chosen, text_tokens, title, text)
            best_node = None
            best_score = 0.0
            for node in nodes:
                score = _score_outline_node(node, text_tokens, title, text)
                if score > best_score or (score == best_score and best_node is not None and node.depth > best_node.depth):
                    best_score = score
                    best_node = node

            if chosen_score <= 0 or (best_node is not None and best_score > chosen_score):
                logger.warning(
                    "LLM suggested node id=%s title='%s' score=%s but best keyword node id=%s title='%s' score=%s; falling back to keyword matcher.",
                    chosen.id,
                    chosen.title,
                    chosen_score,
                    best_node.id if best_node else None,
                    best_node.title if best_node else None,
                    best_score,
                )
                return _choose_outline_node_by_keywords(course, title, text)
            logger.info(
                "LLM suggested node id=%s title='%s' score=%s accepted",
                chosen.id,
                chosen.title,
                chosen_score,
            )
    except Exception:
        logger.exception("LLM classification validation error")

    return chosen


def _choose_outline_node_by_keywords(course: CourseGroup, title: str, text: str) -> OutlineNode | None:
    text_tokens = _tokenize(f"{title} {text}")
    best_node = None
    best_score = 0.0

    for node in course.nodes.all().order_by("depth", "order", "id"):
        path_tokens = _tokenize(" ".join(item.title for item in _outline_path(node)))
        if not path_tokens:
            continue

        overlap = len(path_tokens & text_tokens)
        phrase_bonus = 2.0 if node.title.lower() in text.lower() or node.title.lower() in title.lower() else 0.0
        depth_bonus = node.depth * 0.4
        score = overlap + phrase_bonus + depth_bonus

        if score > best_score or (score == best_score and best_node is not None and node.depth > best_node.depth):
            best_score = score
            best_node = node

    return best_node if best_score > 0 else None


def choose_outline_node_for_material(course: CourseGroup, title: str, text: str) -> OutlineNode | None:
    return _choose_outline_node_with_llm(course, title, text) or _choose_outline_node_by_keywords(course, title, text)


def _outline_path(node: OutlineNode | None) -> list[OutlineNode]:
    if node is None:
        return []
    path = [node]
    current = node
    while current.parent_id:
        current = current.parent
        path.append(current)
    return list(reversed(path))


def _outline_context_for_material(material: LearningMaterial) -> dict:
    topic_path = _outline_path(material.outline_node)
    module = material.module_node or (topic_path[0] if topic_path else None)
    siblings = []
    if material.outline_node_id:
        siblings = list(
            OutlineNode.objects.filter(
                course=material.course,
                parent_id=material.outline_node.parent_id,
            )
            .exclude(pk=material.outline_node_id)
            .order_by("order", "id")
            .values_list("title", flat=True)
        )
    return {
        "course_title": material.course.title,
        "module_title": module.title if module else "",
        "module_related_info": module.related_info if module else {},
        "topic_title": material.outline_node.title if material.outline_node else "",
        "topic_related_info": material.outline_node.related_info if material.outline_node else {},
        "topic_path": " / ".join(node.title for node in topic_path),
        "sibling_topics": siblings,
    }


def generate_lesson_metadata_from_text(text: str, outline_context: dict | None = None) -> dict:
    outline_context = outline_context or {}
    prompt = textwrap.dedent(
        f"""
        You are organizing teacher-provided PDF lesson text.

        AUTHORITATIVE TEACHER COURSE OUTLINE CONTEXT:
        Course: {outline_context.get("course_title") or "Not provided"}
        Module: {outline_context.get("module_title") or "Not provided"}
        Module related teacher info: {_format_json_for_prompt(outline_context.get("module_related_info") or {})}
        Selected topic: {outline_context.get("topic_title") or "Not provided"}
        Selected topic related teacher info: {_format_json_for_prompt(outline_context.get("topic_related_info") or {})}
        Outline path: {outline_context.get("topic_path") or "Not provided"}
        Sibling topics under the same parent: {", ".join(outline_context.get("sibling_topics") or []) or "None listed"}

        TASK:
        Create metadata only. Do not rewrite, paraphrase, simplify, summarize, or generate lesson body content.
        The teacher outline above is already approved. Do not decide that this PDF belongs to another module or topic.

        OUTPUT JSON ONLY:
        {{
          "lesson_title": "Short lesson title",
          "concepts": [
            {{
              "canonical_title": "Concept title",
              "description": "Short concept metadata description",
              "source_excerpt": "Exact copied excerpt from the PDF text",
              "confidence": 0.9
            }}
          ]
        }}

        RULES:
        - If a selected topic is provided, lesson_title should use that selected topic unless the PDF has a clearer title for the same topic.
        - Never use a sibling topic, parent module, or unrelated heading as lesson_title.
        - source_excerpt must be copied exactly from the PDF_TEXT.
        - Do not create narration or learning object content.
        - Do not infer a new parent-child relationship. The outline path is fixed by the teacher.
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


def _is_same_label(left: str, right: str) -> bool:
    return _normalized_heading_label(left) == _normalized_heading_label(right)


def _remove_outline_container_objects(learning_objects: list[dict], outline_context: dict) -> list[dict]:
    module_title = outline_context.get("module_title") or ""
    topic_title = outline_context.get("topic_title") or ""
    if not module_title:
        return learning_objects

    cleaned = []
    for item in learning_objects:
        title = item.get("title") or ""
        content = item.get("content") or ""
        if (
            item.get("type") == "lesson_content"
            and _is_same_label(title, module_title)
            and topic_title
            and not _is_same_label(module_title, topic_title)
        ):
            item = item.copy()
            item["title"] = topic_title
            item["source_outline_container_title"] = module_title
        if _is_same_label(title, module_title) and not content.strip():
            continue
        cleaned.append(item)

    for order, item in enumerate(cleaned):
        item["order"] = order
    return cleaned


def refine_learning_object_titles_with_llm(
    learning_objects: list[dict],
    lesson_title: str = "",
    outline_context: dict | None = None,
) -> list[dict]:
    candidates = [
        {
            "index": index,
            "current_title": item.get("title", ""),
            "content": _limited_text(item.get("content", ""), 900),
            "type": item.get("type", ""),
        }
        for index, item in enumerate(learning_objects)
        if item.get("type") == "lesson_content" and item.get("content", "").strip()
    ]
    if not candidates:
        return learning_objects

    outline_context = outline_context or {}
    prompt = textwrap.dedent(
        f"""
        Review extracted learning object titles from a teacher-provided PDF.

        LESSON TITLE:
        {lesson_title or "Not provided"}

        AUTHORITATIVE OUTLINE CONTEXT:
        Course: {outline_context.get("course_title") or "Not provided"}
        Module: {outline_context.get("module_title") or "Not provided"}
        Selected topic: {outline_context.get("topic_title") or "Not provided"}
        Outline path: {outline_context.get("topic_path") or "Not provided"}

        TASK:
        Make each learning object title understandable as a standalone card title.
        Use the current title, its exact teacher-provided content, and the outline context.

        OUTPUT JSON ONLY:
        {{
          "items": [
            {{
              "index": 0,
              "title": "Standalone title"
            }}
          ]
        }}

        RULES:
        - Rewrite titles only when the current title is too vague by itself, such as a title that depends on nearby cards.
        - Do not rewrite, paraphrase, summarize, delete, or add learning object content.
        - Do not invent facts outside the provided title, content, and outline context.
        - Keep teacher terminology when possible.
        - Return one item for every candidate index.
        - Titles should be short but complete enough to understand without seeing another card.

        CANDIDATES:
        {json_dumps_for_prompt(candidates)}
        """
    ).strip()

    try:
        response = get_llm_client().generate_text(prompt, max_tokens=1200, timeout=180)
        output = response.get("text") if isinstance(response, dict) else None
        data = extract_json_from_text(output) if output else response
    except Exception:
        return learning_objects

    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return learning_objects

    updated = [item.copy() for item in learning_objects]
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
        if 0 <= index < len(updated) and title:
            updated[index]["title"] = title[:255]

    for order, item in enumerate(updated):
        item["order"] = order
    return updated


def describe_pdf_images(images: list[dict], lesson_title: str = "", nearby_text: str = "") -> list[dict]:
    client = get_llm_client()
    descriptions = []
    for image in images:
        prompt = textwrap.dedent(
            f"""
            Describe only the visible content in this image for a blind or visually impaired elementary learner.

            PAGE_NUMBER:
            {image["page_number"]}

            IMAGE_INDEX:
            {image["index"] + 1}

            OUTPUT JSON ONLY:
            {{
              "description": "One short sentence describing only what is visible in the image",
              "educational_purpose": "One short phrase describing what this image shows",
              "contains_text": false,
              "visible_text": ""
            }}

            RULES:
            - Use one short clear sentence (no more than ~20 words) for `description`.
            - Describe only what is literally visible in the picture.
            - Do not add lesson context, topic meaning, explanations, or background information.
            - Do not mention the lesson title, module, topic, or learning objective unless those exact words appear in the image.
            - Do not invent anything not visible in the image.
            - Transcribe every readable word, label, caption, legend, axis label, table cell, and number inside the image into `visible_text`.
            - Keep `visible_text` close to the image's original wording. Do not summarize visible text.
            - If no readable text is visible, set `contains_text` to false and `visible_text` to an empty string.
            """
        ).strip()

        response = client.describe_image(image["image_bytes"], prompt, max_tokens=700, timeout=300)
        output = response.get("text") if isinstance(response, dict) else None
        data = extract_json_from_text(output) if output else None
        if not isinstance(data, dict):
            data = {
                "description": output or "No local vision description was generated.",
                "educational_purpose": "",
                "contains_text": False,
                "visible_text": "",
            }

        description = data.get("description", "")
        visible_text = data.get("visible_text", "")
        # Post-process to keep descriptions concise and remove extraneous context.
        if description:
            sentences = re.split(r"(?<=[.!?])\s+", description.strip())
            first = sentences[0].strip()
            first = re.sub(r'^(?:The image shows|This image shows)\s*', "", first, flags=re.IGNORECASE)
            words = first.split()
            if len(words) > 30:
                first = " ".join(words[:30]) + "..."
            description = first
        descriptions.append(
            {
                "page": image["page_number"],
                "image_index": image["index"],
                "page_number": image["page_number"],
                "index": image["index"],
                "width": image["width"],
                "height": image["height"],
                "extension": image["extension"],
                "description": description,
                "content": _format_image_learning_content(description, visible_text),
                "educational_purpose": data.get("educational_purpose", ""),
                "contains_text": bool(data.get("contains_text")),
                "visible_text": visible_text,
                "source": "local_vision_model",
                "llm_metadata": response.get("llm_metadata") if isinstance(response, dict) else client.metadata,
            }
        )
    return descriptions


def _format_image_learning_content(description: str, visible_text: str = "") -> str:
    description = re.sub(r"\s+", " ", description or "").strip()
    visible_text = re.sub(r"\s+", " ", visible_text or "").strip()
    if description and visible_text:
        return f"{description}\n\nVisible text: {visible_text}"
    return description or (f"Visible text: {visible_text}" if visible_text else "")


def _title_from_teacher_text(content: str, fallback: str) -> str:
    first_sentence = re.split(r"(?<=[.!?])\s+", content.strip())[0]
    return (first_sentence[:80].strip(" .") or fallback)[:255]


def _inline_definition_split(text: str) -> tuple[str, str] | None:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text or len(text) > 700:
        return None
    match = re.match(r"^([A-Z][A-Za-z0-9 /,&()]{1,70})\s*(?::|[-–—])\s+(.+)$", text)
    if not match:
        return None
    title = match.group(1).strip(" .:-–—")
    content = match.group(2).strip()
    if not title or not content or len(content.split()) < 4:
        return None
    if re.search(r"\b[A-Z][A-Za-z0-9 /,&()]{1,40}\s*:", content):
        return None
    if not re.match(r"^(?:a|an|the|is|are|refers?\b|means?\b|describes?\b|includes?\b)", content, flags=re.IGNORECASE):
        return None
    if _is_admin_or_system_support_text(title):
        return None
    return title[:255], content


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
    title_like = text.endswith("?") or title_cased_count >= (1 if word_count == 1 else max(2, len(title_words) - 1))
    if 1 <= word_count <= 9 and re.search(r"[A-Za-z]", text) and title_like:
        return text.strip(" .")[:255]
    return None


def _raw_learning_object_heading_title(block: dict) -> str | None:
    if block.get("teacher_override"):
        return None
    text = block.get("text", "")
    if int(block.get("line_count") or 1) >= 2 and not _section_heading_title(text):
        return None
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


def _normalized_heading_label(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _starts_excluded_section(text: str) -> bool:
    if _is_admin_or_system_support_text(text) or _is_question_or_activity_text(text):
        return True

    label = _normalized_heading_label(text)
    excluded_section_labels = {
        "learning objective",
        "learning objectives",
        "objective",
        "objectives",
        "essential question",
        "essential questions",
        "success criteria",
        "learning goal",
        "learning goals",
        "lesson objective",
        "lesson objectives",
        "teacher notes",
        "teacher note",
    }
    if label in excluded_section_labels or any(label.startswith(f"{section} ") for section in excluded_section_labels):
        return True
    if label.startswith("lesson ") and ":" in text:
        return True
    return False


def _is_learning_objective_statement(text: str) -> bool:
    normalized = _normalized_heading_label(text)
    if not normalized:
        return False
    if re.match(r"^(describe|identify|classify|distinguish|explain|compare|contrast|summarize|outline|define|analyze|evaluate|list|illustrate|recognize|state|demonstrate|interpret|calculate|predict|observe|examine)\b", normalized):
        return True
    if re.match(r"^(students should|learners should|learners will|students will|learners can|students can)\b", normalized):
        return True
    if any(term in normalized for term in ("understand", "know", "recognize", "describe", "identify", "classify", "compare", "contrast", "define", "summarize", "outline")) and len(normalized.split()) <= 20:
        return True
    return False


def _ends_excluded_section(text: str) -> bool:
    stripped = (text or "").strip()
    if re.match(r"^[\u2022\-\*]", stripped):
        return False
    if _is_question_or_activity_text(stripped):
        return False
    if _is_learning_objective_statement(stripped):
        return False
    if _section_heading_title(stripped) or _looks_like_plain_subtopic_heading(stripped):
        return True
    if len(stripped.split()) >= 8:
        return True
    if re.search(r"[.!?]$", stripped) and len(stripped.split()) >= 5:
        return True
    return False


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


def _block_is_excluded_from_learning_object(block: dict) -> bool:
    text = block.get("text", "") or ""
    label = _normalized_heading_label(text)
    if re.search(r"\bpage\s+\d+\b", label) and len(text.split()) <= 8:
        return True
    if label.startswith(("prerequisite connection", "concept dependency")):
        return True
    if _is_admin_or_system_support_text(text):
        return True
    if _is_question_or_activity_text(text):
        return True
    if _is_learning_objective_statement(text):
        return True
    return block.get("category") in {
        "learning_objective",
        "assessment",
        "teacher_note",
        "reference",
        "decorative_or_noise",
    }


def _is_admin_or_system_support_text(text: str) -> bool:
    label = _normalized_heading_label(text)
    if not label:
        return False

    admin_cues = (
        "teacher review note",
        "teacher should verify",
        "local llm may extract",
        "generated learner path",
        "prerequisite edge",
        "edge scoring",
        "concept node",
        "concept nodes",
        "learning path",
        "learner path",
        "dependency cues",
        "prerequisite cues",
    )
    if any(cue in label for cue in admin_cues):
        return True

    support_terms = {"teacher", "llm", "algorithm", "edge", "scoring", "verify"}
    path_terms = {"learner", "learning", "path", "prerequisite", "dependency", "concept", "node", "nodes"}
    tokens = set(label.split())
    return bool(tokens & support_terms) and len(tokens & path_terms) >= 2


def _is_question_or_activity_text(text: str) -> bool:
    stripped = re.sub(r"\s+", " ", text or "").strip()
    label = _normalized_heading_label(stripped)
    if not label:
        return False

    section_labels = {
        "answer",
        "answer key",
        "answers",
        "application",
        "assignment",
        "assignments",
        "question",
        "questions",
        "guide question",
        "guide questions",
        "discussion question",
        "discussion questions",
        "practice question",
        "practice questions",
        "quiz",
        "quizzes",
        "test",
        "tests",
        "assessment",
        "assessments",
        "evaluation",
        "evaluations",
        "activity",
        "activities",
        "learning activity",
        "learning activities",
        "enrichment activity",
        "enrichment activities",
        "practice activity",
        "practice activities",
        "exercise",
        "exercises",
        "worksheet",
        "worksheets",
        "review questions",
        "review",
        "practice",
        "practice exercise",
        "practice exercises",
        "practice task",
        "practice tasks",
        "check your understanding",
        "check your knowledge",
        "test yourself",
        "try this",
        "let us try",
        "lets try",
        "your turn",
        "challenge",
        "drill",
        "directions",
        "direction",
        "instructions",
        "instruction",
        "procedure",
        "procedures",
        "materials",
        "materials needed",
        "task",
        "tasks",
        "performance task",
        "performance tasks",
    }
    if label in section_labels:
        return True
    if any(label.startswith(f"{section} ") for section in section_labels):
        return True
    if stripped.endswith("?"):
        return True

    prompt = re.sub(r"^(?:[\u2022\-\*]\s*)?(?:\d+[\.\)]|[a-zA-Z][\.\)])\s*", "", stripped).strip()
    prompt_label = _normalized_heading_label(prompt)
    if prompt and prompt.endswith("?"):
        return True

    instruction_verbs = (
        "answer",
        "arrange",
        "calculate",
        "choose",
        "circle",
        "classify",
        "compare",
        "complete",
        "conduct",
        "create",
        "define",
        "describe",
        "discuss",
        "draw",
        "encircle",
        "enumerate",
        "explain",
        "fill",
        "give",
        "identify",
        "infer",
        "label",
        "list",
        "make",
        "match",
        "observe",
        "predict",
        "select",
        "solve",
        "underline",
        "write",
    )
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
        "write your answers",
    )
    numbered_or_bulleted_prompt = prompt != stripped
    if re.match(r"^(?:q\d+|question\s*\d+|answer)\b", label):
        return True
    if numbered_or_bulleted_prompt and re.match(rf"^(?:{'|'.join(instruction_verbs)})\b", prompt_label):
        return True
    if any(phrase in prompt_label for phrase in instruction_phrases):
        return True
    return False


def _looks_like_body_text(text: str, block: dict) -> bool:
    text = (text or "").strip()
    if not text or _raw_learning_object_heading_title(block):
        return False
    if block.get("category") in {"concept_metadata", "table_header"}:
        return False
    bullet_text = re.sub(r"^[\u2022\-\*]\s*", "", text).strip()
    word_count = len(text.split())
    if bullet_text != text and bullet_text:
        return True
    if int(block.get("line_count") or 1) >= 2 and word_count >= 3:
        return True
    if word_count < 8:
        return False
    return bool(re.search(r"[.!;:)]$", text) or ":" in text) or int(block.get("line_count") or 1) >= 2


def _block_is_kept_content(block: dict) -> bool:
    text = block.get("text", "") or ""
    if _block_is_excluded_from_learning_object(block):
        return False
    return (
        block.get("category") == "lesson_content"
        and block.get("include_in_narration")
        and not _raw_learning_object_heading_title(block)
    ) or _is_instructional_table_or_chart_block(block) or _looks_like_body_text(text, block)


def _heading_has_following_content(blocks: list[dict], start_index: int) -> bool:
    for next_block in blocks[start_index + 1 :]:
        text = (next_block.get("text") or "").strip()
        if not text or _is_image_caption(text):
            continue
        if _block_is_kept_content(next_block):
            return True
        if _raw_learning_object_heading_title(next_block) or next_block.get("category") in {
            "learning_objective",
            "teacher_note",
            "reference",
        }:
            return False
    return False


def _plain_heading_has_following_body_content(blocks: list[dict], start_index: int) -> bool:
    for next_block in blocks[start_index + 1 :]:
        text = (next_block.get("text") or "").strip()
        if not text or _is_image_caption(text):
            continue
        if ":" in text and len(text.split()) >= 3:
            return True
        if _section_heading_title(text) or _looks_like_plain_subtopic_heading(text) or _starts_excluded_section(text):
            return False
        bullet_text = re.sub(r"^[\u2022\-\*]\s*", "", text).strip()
        if bullet_text != text:
            return len(bullet_text.split()) >= 2
        if len(text.split()) >= 3 and (re.search(r"[.!;:)]$", text) or ":" in text or int(next_block.get("line_count") or 1) >= 2):
            return True
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
                "content": image.get("content") or _format_image_learning_content(
                    image.get("description", ""),
                    image.get("visible_text", ""),
                ),
                "source": "local_vision_model",
                "source_page": image.get("page_number") or image.get("page"),
                "source_block_id": None,
                "image_index": image.get("index", index),
                "source_excerpt": image.get("content") or image.get("description", ""),
            }
        )

    current = None
    skipping_excluded_section = False
    for index, block in enumerate(classified_blocks):
        text = block.get("text", "").strip()
        if not text or _is_image_caption(text):
            continue

        if _starts_excluded_section(text):
            _finalize_current_learning_object(current, learning_objects)
            current = None
            skipping_excluded_section = True
            continue
        if skipping_excluded_section:
            if _ends_excluded_section(text):
                skipping_excluded_section = False
            else:
                continue

        inline_definition = _inline_definition_split(text)
        if inline_definition:
            _finalize_current_learning_object(current, learning_objects)
            current = None
            _, content = inline_definition
            learning_objects.append(
                {
                    "order": len(learning_objects),
                    "title": "",
                    "type": "lesson_content",
                    "content": content,
                    "source": "teacher_pdf",
                    "source_page": block.get("page"),
                    "source_block_id": block.get("block_id"),
                    "source_excerpt": text,
                }
            )
            continue

        heading_title = _learning_object_heading_title(block)
        if not heading_title:
            raw_heading_title = _raw_learning_object_heading_title(block)
            if raw_heading_title and _section_heading_title(text) and _heading_has_following_content(classified_blocks, index):
                heading_title = raw_heading_title
            elif raw_heading_title and _plain_heading_has_following_body_content(classified_blocks, index):
                heading_title = raw_heading_title
        if heading_title:
            _finalize_current_learning_object(current, learning_objects)
            current = {
                "order": len(learning_objects),
                "title": "",
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
            elif current is not None and not _block_is_excluded_from_learning_object(block):
                current["parts"].append(text)
                if not current.get("source_excerpt"):
                    current["source_excerpt"] = text
            continue

        if current is None:
            learning_objects.append(
                {
                    "order": len(learning_objects),
                    "title": "",
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


def build_learning_objects_from_pdf_blocks(extracted_blocks: list[dict], image_descriptions: list[dict]) -> list[dict]:
    structural_blocks = []
    for block in extracted_blocks:
        text = (block.get("text") or "").strip()
        inline_definition = _inline_definition_split(text)
        structural_blocks.append(
            {
                **block,
                "category": "lesson_content" if inline_definition else "document_metadata",
                "include_in_narration": bool(inline_definition),
                "confidence": 1.0,
                "reason": "Built structurally from exact PDF text blocks.",
                "source": "teacher_pdf",
            }
        )
    return build_section_learning_objects(structural_blocks, image_descriptions)


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
    if not _material_exists(material):
        raise MaterialDeletedDuringGeneration(
            f"Learning material {material.pk} was deleted before learning objects were saved."
        )
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
    _save_material_update(material, ["generated_json"])
    _sync_learning_objects(material, material.generated_json)
    return material


def generate_material_outputs(material: LearningMaterial) -> LearningMaterial:
    import time as _time
    _t0 = _time.monotonic()

    def _trace(step):
        logger.info("[TRACE material %s] %s (+%ss)", material.id, step, int(_time.monotonic() - _t0))

    try:
        _trace("start: extracting PDF text")
        text = extract_pdf_text(material.pdf_file.path)
        is_image_only_pdf = not _has_enough_embedded_pdf_text(text)
        if is_image_only_pdf:
            _trace("embedded text is sparse; transcribing rendered PDF pages (vision LLM)")
            transcribed_text = transcribe_image_only_pdf_pages(material.pdf_file.path)
            if not transcribed_text.strip():
                raise ValueError("No readable text was found in the PDF, including vision transcription.")
            text = transcribed_text
            extracted_blocks = _text_blocks_from_transcription(transcribed_text)
        else:
            extracted_blocks = extract_pdf_text_blocks(material.pdf_file.path)

        cleaned_preserved_text = clean_pdf_text_for_llm(text, limit=None)
        if not cleaned_preserved_text.strip():
            raise ValueError("No meaningful lesson text was found in the PDF.")
        metadata_text = _limited_text(cleaned_preserved_text)
        extraction_mode = "vision_page_transcription" if is_image_only_pdf else "embedded_pdf_text"
        _trace(f"text extracted: {len(text)} chars, {len(extracted_blocks)} blocks, mode={extraction_mode}")

        images = [] if is_image_only_pdf else extract_meaningful_pdf_images(material.pdf_file.path)
        _trace(f"images extracted: {len(images)}")

        if material.outline_node_id is None:
            _trace("matching outline node")
            matched_node = choose_outline_node_for_material(material.course, material.title, metadata_text)
            if matched_node:
                material.outline_node = matched_node
                if material.module_node_id is None:
                    module_node = matched_node
                    while module_node.parent_id is not None:
                        module_node = module_node.parent
                    material.module_node = module_node

        outline_context = _outline_context_for_material(material)

        _trace("generating lesson metadata (LLM)")
        metadata = generate_lesson_metadata_from_text(metadata_text, outline_context=outline_context)
        lesson_title = outline_context.get("topic_title") or metadata.get("lesson_title") or material.title
        _trace("classifying instructional blocks")
        classified_blocks = classify_instructional_blocks(extracted_blocks)
        _trace(f"describing {len(images)} images (vision LLM)")
        image_descriptions = describe_pdf_images(images, lesson_title, cleaned_preserved_text)
        _trace("building learning objects")
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
        learning_objects = _remove_outline_container_objects(learning_objects, outline_context)
        if os.getenv("LLM_REWRITE_LEARNING_OBJECT_TITLES", "False").lower() in ("1", "true", "yes"):
            _trace("reviewing learning object titles (LLM)")
            learning_objects = refine_learning_object_titles_with_llm(
                learning_objects,
                lesson_title=lesson_title,
                outline_context=outline_context,
            )
        else:
            _trace("skipping LLM title rewrite; preserving PDF-derived learning object titles")
        narration_script = build_narration_script_from_learning_objects(learning_objects)
        playlist = build_lesson_playlist(narration_script)
        generated_json = {
            "generated_json_version": 3,
            "lesson_title": lesson_title,
            "llm_suggested_lesson_title": metadata.get("lesson_title") or "",
            "teacher_outline_context": outline_context,
            "summary": "",
            "original_text": text,
            "cleaned_preserved_text": cleaned_preserved_text,
            "text_extraction_mode": extraction_mode,
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

        material.extracted_text = text
        material.generated_json = generated_json
        material.status = LearningMaterial.Status.COMPLETED
        material.error_message = ""
        _save_material_update(
            material,
            ["outline_node", "module_node", "extracted_text", "generated_json", "status", "error_message"],
        )

        _sync_learning_objects(material, generated_json)
        _trace(f"done: status={material.status}")
    except MaterialDeletedDuringGeneration as exc:
        _trace(f"stopped: {exc}")
    except Exception as exc:
        _trace(f"FAILED: {type(exc).__name__}: {exc}")
        material.status = LearningMaterial.Status.FAILED
        material.error_message = str(exc)
        try:
            _save_material_update(material, ["status", "error_message"])
        except MaterialDeletedDuringGeneration as deleted_exc:
            _trace(f"stopped while saving failure state: {deleted_exc}")

    return material
