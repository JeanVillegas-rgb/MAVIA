from __future__ import annotations

import re
import os
import json
import logging

import fitz
from django.db import DatabaseError
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode

from .instructional_content_classifier import (
    classify_instructional_blocks,
    extract_pdf_text_blocks,
    find_captioned_figure_regions,
    split_classified_blocks,
)
logger = logging.getLogger(__name__)

# Deterministic retrieval threshold for material-to-outline matching.
# Cosine similarity is bounded in [0, 1]; values near 0 are effectively
# no evidence of topical overlap in a sparse bag-of-words vector space.
TFIDF_COSINE_THRESHOLD = 0.01


class MaterialDeletedDuringGeneration(RuntimeError):
    pass


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


def clean_pdf_text_for_extraction(text: str, limit: int | None = 12000) -> str:
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
    cleaned = clean_pdf_text_for_extraction(text, limit=None)
    words = re.findall(r"[A-Za-z0-9]+", cleaned)
    return len(cleaned) >= 120 and len(words) >= 25


def transcribe_image_only_pdf_pages(file_path: str) -> str:
    """Deterministic fallback for image-only PDFs: use PDF text extraction only."""
    document = fitz.open(file_path)
    try:
        pages = []
        for page_index, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            if text:
                pages.append(f"Page {page_index}\n{text}")
        return "\n\n".join(pages).strip()
    finally:
        document.close()


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
                    "source": "deterministic_page_text_fallback",
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

                crop_bytes = None
                if bbox and width > 0 and height > 0:
                    try:
                        crop_pixmap = page.get_pixmap(clip=fitz.Rect(bbox), dpi=150)
                        crop_bytes = crop_pixmap.tobytes("png")
                    except Exception:
                        crop_bytes = None

                images.append(
                    {
                        "page_number": page_index,
                        "index": len(images),
                        "block_index": block_index,
                        "width": int(width),
                        "height": int(height),
                        "area": int(image_area),
                        "extension": "png",
                        "image_bytes": crop_bytes or image_bytes,
                        "bbox": tuple(float(value) for value in bbox),
                    }
                )

            for region in find_captioned_figure_regions(page, page_dict):
                bbox = fitz.Rect(region["bbox"])
                try:
                    crop_bytes = page.get_pixmap(clip=bbox, dpi=150, alpha=False).tobytes("png")
                except (RuntimeError, ValueError):
                    continue
                deduplicated = []
                for existing in images:
                    existing_bbox = existing.get("bbox")
                    if existing.get("page_number") != page_index or not existing_bbox:
                        deduplicated.append(existing)
                        continue
                    existing_rect = fitz.Rect(existing_bbox)
                    intersection = existing_rect & bbox
                    intersection_area = max(intersection.width, 0) * max(intersection.height, 0)
                    smaller_area = min(
                        max(existing_rect.width * existing_rect.height, 1),
                        max(bbox.width * bbox.height, 1),
                    )
                    if intersection_area / smaller_area < 0.5:
                        deduplicated.append(existing)
                images = deduplicated
                images.append(
                    {
                        "page_number": page_index,
                        "index": len(images),
                        "block_index": None,
                        "width": int(bbox.width),
                        "height": int(bbox.height),
                        "area": int(bbox.width * bbox.height),
                        "extension": "png",
                        "image_bytes": crop_bytes,
                        "bbox": tuple(bbox),
                        "visible_text": region.get("visible_text", ""),
                        "caption": region.get("caption", ""),
                    }
                )
    finally:
        document.close()
    selected = sorted(images, key=lambda item: item["area"], reverse=True)[:max_images]
    for index, image in enumerate(selected):
        image["index"] = index
    return selected


def save_extracted_pdf_images(images: list[dict], material_id: int) -> list[dict]:
    from pathlib import Path
    from django.conf import settings
    media_dir = Path(settings.MEDIA_ROOT) / "extracted_images"
    media_dir.mkdir(parents=True, exist_ok=True)

    for img in images:
        if img.get("image_bytes"):
            filename = f"mat_{material_id}_img_{img['index'] + 1}.png"
            filepath = media_dir / filename
            with open(filepath, "wb") as f:
                f.write(img["image_bytes"])
            img["image_url"] = f"{settings.MEDIA_URL}extracted_images/{filename}"
    return images


def _limited_text(text: str, limit: int = 12000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit]


def _outline_node_similarity_text(node: OutlineNode) -> str:
    """Build a deterministic retrieval document for an outline node.

    The material-to-outline selection is a lexical-matching task, so the node
    document must expose the same vocabulary that a teacher would recognize as
    the topic's title and contextual information. We intentionally keep the
    surface form simple and explainable rather than injecting a probabilistic
    semantic model.
    """
    path = _outline_path(node)
    path_titles = [item.title for item in path]
    related_info = []

    for item in path:
        if isinstance(item.related_info, dict):
            related_info.append(json.dumps(item.related_info, ensure_ascii=False))

    return " ".join([node.title, *path_titles, *related_info]).strip()


def _choose_outline_node_by_tfidf(course: CourseGroup, title: str, text: str) -> OutlineNode | None:
    """Match a learning material to the most similar outline node.

    The algorithm is intentionally unchanged in substance: it represents a
    single query document and a corpus of outline-node documents as TF-IDF
    vectors, then measures L2-normalized lexical overlap with cosine similarity.

    The refactor makes the retrieval contract explicit:
        1. Clean the material text.
        2. Build a node document for each outline node.
        3. Learn one vectorizer on the combined corpus.
        4. Rank the nodes only by cosine similarity.
        5. Reject matches whose score is lower than the configured confidence
           floor because a positive but tiny angle similarity is not stable IR
           evidence.
    """
    nodes = list(course.nodes.all().order_by("depth", "order", "id"))
    if not nodes:
        return None

    material_text = clean_pdf_text_for_extraction(f"{title}\n{text}", limit=16000)
    if not material_text.strip():
        return None

    node_documents = [_outline_node_similarity_text(node) for node in nodes]
    if not any(document.strip() for document in node_documents):
        return None

    try:
        vectorizer = TfidfVectorizer(
            analyzer="word",
            ngram_range=(1, 2),
            stop_words="english",
            lowercase=True,
            min_df=1,
        )
        matrix = vectorizer.fit_transform([material_text, *node_documents])
    except (ValueError, TypeError):
        return None

    scores = cosine_similarity(matrix[0:1], matrix[1:]).flatten()
    if scores.size == 0:
        return None

    ranked = sorted(
        zip(nodes, scores),
        key=lambda item: float(item[1]),
        reverse=True,
    )

    best_node, best_score = ranked[0]
    best_score = float(best_score)

    if best_score <= TFIDF_COSINE_THRESHOLD:
        return None

    return best_node


def choose_outline_node_for_material(course: CourseGroup, title: str, text: str) -> OutlineNode | None:
    """Public retrieval wrapper used by the generation pipeline.

    This exposes the deterministic document matching API and preserves the
    public contract expected by the rest of the lessons generation stack.
    """
    return _choose_outline_node_by_tfidf(course, title, text)


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
    lesson_title = outline_context.get("topic_title") or outline_context.get("module_title") or "Lesson"
    return {
        "lesson_title": lesson_title,
        "concepts": [],
    }


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


def remove_structural_metadata_learning_objects(learning_objects: list[dict]) -> list[dict]:
    cleaned = [
        item
        for item in learning_objects
        if not is_structural_metadata_label(item.get("title") or "")
    ]
    for order, item in enumerate(cleaned):
        item["order"] = order
    return cleaned


def refine_learning_object_titles(
    learning_objects: list[dict],
    lesson_title: str = "",
    outline_context: dict | None = None,
) -> list[dict]:
    return learning_objects


def describe_pdf_images(images: list[dict], lesson_title: str = "", nearby_text: str = "") -> list[dict]:
    descriptions = []
    for image in images:
        description = image.get("description") or ""
        visible_text = image.get("visible_text") or ""
        content = description
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
                "content": content,
                "image_url": image.get("image_url", ""),
                "educational_purpose": "",
                "contains_text": bool(visible_text),
                "visible_text": visible_text,
                "caption": image.get("caption") or "",
                "source": "image_pdf",
            }
        )
    return descriptions


def _format_image_learning_content(description: str, visible_text: str = "") -> str:
    description = re.sub(r"\s+", " ", description or "").strip()
    visible_text = re.sub(r"\s+", " ", visible_text or "").strip()
    if description and visible_text:
        return f"{description}\n\nVisible text: {visible_text}"
    return description or (f"Visible text: {visible_text}" if visible_text else "")


def _title_from_teacher_text(content: str, fallback: str = "Untitled content") -> str:
    first_sentence = re.split(r"(?<=[.!?])\s+", (content or "").strip())[0]
    title = first_sentence[:80].strip(" .")
    return (title or fallback)[:255]


def _title_from_learning_object_item(item: dict, fallback: str = "Untitled content") -> str:
    return _title_from_teacher_text(
        item.get("content") or item.get("source_excerpt") or item.get("description") or item.get("visible_text") or "",
        fallback,
    )


def _inline_definition_split(text: str) -> tuple[str, str] | None:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"^[\u2022\u25cf\u25aa\uf0b7]\s*", "", text)
    if not text or len(text) > 700:
        return None
    match = re.match(
        r"^(?:(?:\d+[\.\)]|[\-\*•●])\s*)?([A-Z][A-Za-z0-9 /,&()]{1,70})\s*(?::|[-–—])\s+(.+)$",
        text,
    )
    if not match:
        return None
    title = match.group(1).strip(" .:-–—")
    content = match.group(2).strip()
    if not title or not content or len(content.split()) < 3:
        return None
    if _is_admin_or_system_support_text(title):
        return None
    return title[:255], content


def _followed_by_another_inline_definition(blocks: list[dict], index: int) -> bool:
    """Return whether this labeled line begins a group of sibling definitions."""
    for next_block in blocks[index + 1 :]:
        next_text = (next_block.get("text") or "").strip()
        if not next_text or _is_image_caption(next_text):
            continue
        if is_structural_metadata_label(next_text) or _starts_excluded_section(next_text):
            return False
        if next_block.get("category") != "lesson_content" or not next_block.get("include_in_narration"):
            return False
        return _inline_definition_split(next_text) is not None
    return False


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
    if is_structural_metadata_label(text):
        return None
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


def is_structural_metadata_label(text: str) -> bool:
    label = _normalized_heading_label(text)
    if not label:
        return False
    return bool(
        re.fullmatch(
            r"(?:module|unit|chapter|lesson|week|quarter|section|part)\s+"
            r"(?:\d+|[ivxlcdm]+)(?:\s+.+)?",
            label,
        )
    )


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
    objective_action_verbs = (
        "analyze",
        "calculate",
        "classify",
        "compare",
        "contrast",
        "define",
        "demonstrate",
        "describe",
        "distinguish",
        "evaluate",
        "examine",
        "explain",
        "group",
        "identify",
        "illustrate",
        "interpret",
        "list",
        "observe",
        "outline",
        "predict",
        "recognize",
        "relate",
        "state",
        "summarize",
    )
    if re.match(rf"^(?:{'|'.join(objective_action_verbs)})\b", normalized):
        return True
    if re.match(r"^(students should|learners should|learners will|students will|learners can|students can)\b", normalized):
        return True
    return False


def _ends_excluded_section(block: dict, blocks: list[dict], index: int) -> bool:
    stripped = (block.get("text") or "").strip()
    if re.match(r"^[\u2022\-\*]", stripped):
        return False
    if _is_question_or_activity_text(stripped):
        return False

    heading = _raw_learning_object_heading_title(block)
    if heading and (
        (_section_heading_title(stripped) and _heading_has_following_content(blocks, index))
        or _plain_heading_has_following_body_content(blocks, index)
    ):
        return True

    # When visual heading information is unavailable, a wrapped paragraph is
    # the structural fallback for the start of lesson prose. Single-line items
    # remain part of the excluded section regardless of their opening verb.
    if int(block.get("line_count") or 1) >= 2 and len(stripped.split()) >= 5:
        return True
    return False


def _finalize_current_learning_object(current: dict | None, learning_objects: list[dict]) -> None:
    if not current:
        return
    parts = current.pop("parts", [])
    content = _format_section_content(parts)
    if content:
        current["content"] = content
        if not (current.get("title") or "").strip():
            current["title"] = _title_from_teacher_text(content)
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
        "local outline extraction may detect",
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

    support_terms = {"teacher", "algorithm", "edge", "scoring", "verify"}
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
    if any(re.fullmatch(rf"{re.escape(section)}\s+\d+", label) for section in section_labels):
        return True
    if any(
        re.match(rf"^\s*{re.escape(section)}\s*[:\-â€“â€”]\s*\S", stripped, flags=re.IGNORECASE)
        for section in section_labels
    ):
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


def _append_pdf_text_to_learning_object(item: dict, text: str, block: dict) -> bool:
    """Attach an extracted PDF block without rewriting its text."""
    if item.get("type") != "lesson_content" or not text.strip():
        return False

    existing = (item.get("content") or "").strip()
    item["content"] = _format_section_content([existing, text])
    if not item.get("source_excerpt"):
        item["source_excerpt"] = text
    if not item.get("source_page"):
        item["source_page"] = block.get("page")
    return True


def build_section_learning_objects(classified_blocks: list[dict], image_descriptions: list[dict]) -> list[dict]:
    learning_objects = []
    has_section_headings = any(_learning_object_heading_title(block) for block in classified_blocks)
    figure_titles = [
        _image_caption_title(block.get("text", ""))
        for block in classified_blocks
        if _image_caption_title(block.get("text", ""))
    ]

    for index, image in enumerate(image_descriptions):
        # This is the teacher/input contract: an image object has a narrative
        # description that the teacher can author. Therefore the title must come
        # from that one-line description whenever it exists. If the teacher did
        # not provide a description, we keep the neutral fallback label.
        description = (image.get("description") or "").strip()
        caption = (image.get("caption") or "").strip()
        if description:
            title = _title_from_teacher_text(description, "Extracted image")
        elif caption:
            title = _image_caption_title(caption) or _title_from_teacher_text(caption, "Extracted image")
        else:
            title = _title_from_teacher_text(image.get("visible_text") or image.get("content") or "", "Extracted image")

        if "content" in image:
            image_content = image.get("content") or ""
        else:
            image_content = description

        learning_objects.append(
            {
                "order": len(learning_objects),
                "title": title,
                "type": "image_description",
                "image_url": image.get("image_url") or "",
                "content": image_content,
                "source": "teacher_image_description",
                "source_page": image.get("page_number") or image.get("page"),
                "source_block_id": None,
                "image_index": image.get("index", index),
                "source_excerpt": caption or image.get("visible_text") or description,
            }
        )

    current = None
    active_section_title = ""
    skipping_excluded_section = False
    for index, block in enumerate(classified_blocks):
        text = block.get("text", "").strip()
        if not text or _is_image_caption(text):
            continue

        if is_structural_metadata_label(text):
            _finalize_current_learning_object(current, learning_objects)
            current = None
            active_section_title = ""
            continue

        if _starts_excluded_section(text):
            _finalize_current_learning_object(current, learning_objects)
            current = None
            active_section_title = ""
            skipping_excluded_section = True
            continue
        if skipping_excluded_section:
            if _ends_excluded_section(block, classified_blocks, index):
                skipping_excluded_section = False
            else:
                continue

        inline_definition = None
        if block.get("category") == "lesson_content" and block.get("include_in_narration"):
            inline_definition = _inline_definition_split(text)
        if inline_definition:
            followed_by_sibling = _followed_by_another_inline_definition(classified_blocks, index)
            if current is not None:
                if followed_by_sibling:
                    active_section_title = current.get("title", "")
                    if current.get("parts"):
                        current["section_title"] = active_section_title
                        _finalize_current_learning_object(current, learning_objects)
                    current = None
                elif not current.get("parts"):
                    # One labeled definition directly below an empty heading
                    # defines that heading and stays verbatim as its body.
                    current["parts"].append(text)
                    if not current.get("source_excerpt"):
                        current["source_excerpt"] = text
                    continue
            _finalize_current_learning_object(current, learning_objects)
            current = None
            title, content = inline_definition
            learning_objects.append(
                {
                    "order": len(learning_objects),
                    "section_title": active_section_title,
                    "title": title,
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
            active_section_title = ""
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
            elif current is not None and not _block_is_excluded_from_learning_object(block):
                current["parts"].append(text)
                if not current.get("source_excerpt"):
                    current["source_excerpt"] = text
            continue

        if current is None:
            # Once the PDF has established concept headings, a body block that
            # has no new heading is continuation text for the preceding concept.
            # Keeping it there prevents extraction/layout fragments from being
            # promoted into made-up learning-object titles.
            if has_section_headings and learning_objects:
                if _append_pdf_text_to_learning_object(learning_objects[-1], text, block):
                    continue

            # A genuinely unheaded document still needs one usable card. Its
            # title is copied from its own first sentence, never generated.
            learning_objects.append(
                {
                    "order": len(learning_objects),
                    "title": _title_from_teacher_text(text),
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
    """Build learning objects from raw PDF text blocks.

    The raw extraction step must not pre-judge every block as either content or
    metadata. Those category assignments belong to the deterministic classifier,
    which is responsible for deciding whether a block is instructional, heading,
    reference, noise, or another role. This function therefore consults the
    classifier on the full block stream and then hands the labeled evidence to
    the section builder that assembles learning objects.
    """
    classified_blocks = classify_instructional_blocks(extracted_blocks)
    return build_section_learning_objects(classified_blocks, image_descriptions)


def build_narration_script_from_learning_objects(learning_objects: list[dict]) -> list[dict]:
    narration = []
    previous_section_title = ""
    for item in learning_objects:
        section_title = (item.get("section_title") or "").strip()
        title = (item.get("title") or "").strip()
        content = (item.get("content") or "").strip()
        if not content:
            continue
        if section_title:
            if section_title != previous_section_title:
                if _is_same_label(title, section_title):
                    content = f"{section_title}. {content}".strip()
                else:
                    content = f"In {section_title}. {title}: {content}".strip()
            else:
                content = f"{title}: {content}".strip()
        narration.append(
            {
                "order": len(narration) + 1,
                "type": item.get("type"),
                "section_title": section_title,
                "title": title,
                "page": item.get("source_page"),
                "content": content,
                "source": item.get("source"),
                "source_block_id": item.get("source_block_id"),
                "image_index": item.get("image_index"),
            }
        )
        previous_section_title = section_title
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
                "source": "teacher_image_description",
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
                "source": "teacher_image_description",
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
                "title": _title_from_teacher_text(item.get("content", "")),
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
                        _title_from_teacher_text(item.get("content", ""))
                        if item.get("type") in {"teacher_text", "lesson_content"}
                        else _title_from_teacher_text(item.get("content", ""), "Extracted image")
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
    return learning_objects


def json_dumps_for_prompt(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)


def _sync_learning_objects(material: LearningMaterial, generated_json: dict):
    if not _material_exists(material):
        raise MaterialDeletedDuringGeneration(
            f"Learning material {material.pk} was deleted before learning objects were saved."
        )
    material.learning_objects.all().delete()
    synced_learning_objects = []
    for index, item in enumerate(generated_json.get("learning_objects", [])):
        if is_structural_metadata_label(item.get("title") or ""):
            continue
        is_image = item.get("type") in {"image_description", "image"} or bool(item.get("image_url"))
        kind = LearningObject.Kind.IMAGE if is_image else LearningObject.Kind.TEXT
        learning_object = LearningObject.objects.create(
            material=material,
            kind=kind,
            section_title=(item.get("section_title") or "")[:255],
            title=(item.get("title") or _title_from_learning_object_item(item, "Extracted image" if is_image else "Untitled content"))[:255],
            content=item.get("content") or "",
            image_url=item.get("image_url") or "",
            order=index,
        )
        synced_learning_objects.append(
            {
                **item,
                "order": index,
                "kind": kind,
                "learning_object_id": learning_object.id,
            }
        )
    generated_json["learning_objects"] = synced_learning_objects
    material.generated_json = generated_json
    _save_material_update(material, ["generated_json"])


def rebuild_generated_outputs_from_classifications(generated_json: dict) -> dict:
    classified_blocks = generated_json.get("classified_blocks") or []
    image_descriptions = generated_json.get("image_descriptions") or []
    sections = split_classified_blocks(classified_blocks)
    learning_objects = remove_structural_metadata_learning_objects(
        build_section_learning_objects(classified_blocks, image_descriptions)
    )
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
            _trace("embedded text is sparse; using deterministic PDF page extraction fallback")
            transcribed_text = transcribe_image_only_pdf_pages(material.pdf_file.path)
            if not transcribed_text.strip():
                raise ValueError("No readable text was found in the PDF.")
            text = transcribed_text
            extracted_blocks = _text_blocks_from_transcription(transcribed_text)
        else:
            extracted_blocks = extract_pdf_text_blocks(material.pdf_file.path)

        cleaned_preserved_text = clean_pdf_text_for_extraction(text, limit=None)
        if not cleaned_preserved_text.strip():
            raise ValueError("No meaningful lesson text was found in the PDF.")
        metadata_text = _limited_text(cleaned_preserved_text)
        extraction_mode = "deterministic_page_text_fallback" if is_image_only_pdf else "embedded_pdf_text"
        _trace(f"text extracted: {len(text)} chars, {len(extracted_blocks)} blocks, mode={extraction_mode}")

        images = [] if is_image_only_pdf else extract_meaningful_pdf_images(material.pdf_file.path)
        if images and material.id:
            images = save_extracted_pdf_images(images, material.id)
        _trace(f"images extracted: {len(images)}")

        auto_classified = False
        if material.outline_node_id is None:
            _trace("matching outline node")
            matched_node = choose_outline_node_for_material(material.course, material.title, metadata_text)
            if matched_node:
                material.outline_node = matched_node
                auto_classified = True
                if material.module_node_id is None:
                    module_node = matched_node
                    while module_node.parent_id is not None:
                        module_node = module_node.parent
                    material.module_node = module_node

        outline_context = _outline_context_for_material(material)

        _trace("generating lesson metadata")
        metadata = generate_lesson_metadata_from_text(metadata_text, outline_context=outline_context)
        lesson_title = outline_context.get("topic_title") or metadata.get("lesson_title") or material.title
        _trace("classifying instructional blocks")
        classified_blocks = classify_instructional_blocks(extracted_blocks)
        _trace(f"recording {len(images)} images for teacher descriptions")
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
        learning_objects = remove_structural_metadata_learning_objects(learning_objects)
        learning_objects = refine_learning_object_titles(
            learning_objects,
            lesson_title=lesson_title,
            outline_context=outline_context,
        )
        narration_script = build_narration_script_from_learning_objects(learning_objects)
        playlist = build_lesson_playlist(narration_script)
        generated_json = {
            "generated_json_version": 3,
            "lesson_title": lesson_title,
            "suggested_lesson_title": metadata.get("lesson_title") or "",
            "teacher_outline_context": outline_context,
            "classification_method": "tfidf_cosine_similarity" if auto_classified else "teacher_selected_topic",
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
            "preservation_metadata": {
                "teacher_text_preserved": True,
                "teacher_text_rewritten": False,
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
