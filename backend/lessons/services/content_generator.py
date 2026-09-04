from __future__ import annotations

import re
import os
import json
import logging
import unicodedata
import contextlib
import io

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
from .outline_parser import is_course_outline_document
logger = logging.getLogger(__name__)

def _configured_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Deterministic retrieval settings remain configurable for different curricula.
TFIDF_COSINE_THRESHOLD = _configured_float("MATERIAL_TOPIC_COSINE_THRESHOLD", 0.08)
TFIDF_BROAD_MATCH_RATIO = _configured_float("MATERIAL_TOPIC_BROAD_MATCH_RATIO", 0.60)
TFIDF_MAX_BROAD_MATCHES = int(_configured_float("MATERIAL_TOPIC_MAX_BROAD_MATCHES", 3))
SELECTED_TOPIC_COSINE_THRESHOLD = _configured_float(
    "MATERIAL_SELECTED_TOPIC_COSINE_THRESHOLD",
    0.07,
)
SELECTED_TOPIC_BEST_SCORE_RATIO = _configured_float(
    "MATERIAL_SELECTED_TOPIC_BEST_SCORE_RATIO",
    0.40,
)


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
    minimum_uncaptioned_page_ratio = min(
        1.0,
        max(0.0, float(os.getenv("MIN_UNCAPTIONED_PDF_IMAGE_PAGE_RATIO", "0.04"))),
    )
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
                # Small uncaptioned raster blocks are commonly publisher logos,
                # mascots, or page ornaments. Captioned visuals are discovered
                # separately below and remain eligible regardless of this size
                # guard, so a real figure with an authored caption is retained.
                if image_area / page_area < minimum_uncaptioned_page_ratio:
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


def _instructional_table_rows(rows: list[list[str | None]]) -> bool:
    """Reject sparse text alignments while accepting real multi-row tables."""
    cleaned_rows = [
        [re.sub(r"\s+", " ", str(cell or "")).strip() for cell in row]
        for row in (rows or [])
    ]
    if len(cleaned_rows) < 3:
        return False
    if any(re.search(r"_{3,}", cell) for row in cleaned_rows for cell in row):
        return False
    column_count = max((len(row) for row in cleaned_rows), default=0)
    if not 2 <= column_count <= 10:
        return False

    populated = sum(bool(cell) for row in cleaned_rows for cell in row)
    occupancy = populated / max(len(cleaned_rows) * column_count, 1)
    useful_rows = sum(sum(bool(cell) for cell in row) >= 2 for row in cleaned_rows)
    header_cells = sum(bool(cell) for cell in cleaned_rows[0])
    cell_word_counts = [len(cell.split()) for row in cleaned_rows for cell in row if cell]
    return bool(
        header_cells >= 2
        and useful_rows >= max(3, int(len(cleaned_rows) * 0.60))
        and occupancy >= 0.45
        and cell_word_counts
        and max(cell_word_counts) <= 40
    )


def _borderless_table_regions(page_dict: dict, page_rect: fitz.Rect) -> list[dict]:
    """Find table-like runs whose cells share baselines but have no drawn grid.

    PyMuPDF groups side-by-side cells on one visual row as separate ``lines``
    inside a single text block. Normal wrapped prose has different line y
    coordinates, so baseline and repeated-column geometry distinguish the two
    without depending on any subject-specific words.
    """
    row_candidates = []
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0 or not block.get("bbox"):
            continue
        cells = []
        for line in block.get("lines", []):
            bbox = line.get("bbox")
            if not bbox:
                continue
            text = re.sub(
                r"\s+",
                " ",
                "".join(str(span.get("text") or "") for span in line.get("spans", [])),
            ).strip()
            if text:
                cells.append({"text": text, "rect": fitz.Rect(bbox)})
        if not 2 <= len(cells) <= 10:
            continue

        line_heights = [max(cell["rect"].height, 1.0) for cell in cells]
        typical_height = sorted(line_heights)[len(line_heights) // 2]
        baseline_spread = max(cell["rect"].y0 for cell in cells) - min(cell["rect"].y0 for cell in cells)
        block_rect = fitz.Rect(block["bbox"])
        x_starts = sorted(cell["rect"].x0 for cell in cells)
        if baseline_spread > max(2.5, typical_height * 0.35):
            continue
        if block_rect.height > typical_height * 1.8:
            continue
        if x_starts[-1] - x_starts[0] < page_rect.width * 0.18:
            continue
        row_candidates.append(
            {
                "bbox": block_rect,
                "cells": [cell["text"] for cell in sorted(cells, key=lambda cell: cell["rect"].x0)],
                "x_starts": x_starts,
                "line_height": typical_height,
            }
        )

    groups = []
    current = []
    for row in row_candidates:
        if not current:
            current = [row]
            continue
        previous = current[-1]
        vertical_gap = row["bbox"].y0 - previous["bbox"].y1
        matching_columns = sum(
            any(abs(x_start - prior_x) <= 12.0 for prior_x in previous["x_starts"])
            for x_start in row["x_starts"]
        )
        same_table = (
            0 <= vertical_gap <= max(page_rect.height * 0.06, previous["line_height"] * 4)
            and abs(row["bbox"].x0 - previous["bbox"].x0) <= 12.0
            and matching_columns >= 2
        )
        if same_table:
            current.append(row)
        else:
            if len(current) >= 3:
                groups.append(current)
            current = [row]
    if len(current) >= 3:
        groups.append(current)

    regions = []
    for group in groups:
        rows = [row["cells"] for row in group]
        if not _instructional_table_rows(rows):
            continue
        bbox = fitz.Rect(group[0]["bbox"])
        for row in group[1:]:
            bbox |= row["bbox"]
        regions.append({"rows": rows, "bbox": bbox})
    return regions


def _nearest_table_title(page, table_bbox: fitz.Rect, page_height: float) -> str:
    candidates = []
    for raw_block in page.get_text("blocks"):
        if len(raw_block) < 5:
            continue
        rect = fitz.Rect(raw_block[:4])
        text = re.sub(r"\s+", " ", str(raw_block[4] or "")).strip()
        if not text or len(text.split()) > 14 or re.search(r"[.!?]$", text):
            continue
        gap = table_bbox.y0 - rect.y1
        horizontal_overlap = max(0.0, min(rect.x1, table_bbox.x1) - max(rect.x0, table_bbox.x0))
        if 0 <= gap <= page_height * 0.10 and horizontal_overlap > 0:
            candidates.append((gap, text))
    if candidates:
        return min(candidates, key=lambda item: item[0])[1][:255]
    return ""


def _last_heading_on_page(page) -> str:
    """Return the final authored heading, useful when a table starts next page."""
    candidates = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0 or not block.get("bbox"):
            continue
        text = re.sub(
            r"\s+",
            " ",
            "".join(
                str(span.get("text") or "")
                for line in block.get("lines", [])
                for span in line.get("spans", [])
            ),
        ).strip()
        if not text or len(text.split()) > 14 or re.search(r"[.!?]$", text):
            continue
        spans = [span for line in block.get("lines", []) for span in line.get("spans", [])]
        is_bold = any(
            "bold" in str(span.get("font") or "").casefold() or int(span.get("flags") or 0) & 16
            for span in spans
        )
        is_numbered = bool(re.match(r"^\d+(?:\.\d+)*\.?\s+\S", text))
        if is_bold or is_numbered:
            candidates.append((float(block["bbox"][1]), text))
    if not candidates:
        return ""
    text = max(candidates, key=lambda item: item[0])[1]
    return re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", text).strip()[:255]


def _plausible_table_bbox(bbox: fitz.Rect, page_rect: fitz.Rect, detection_source: str) -> bool:
    """Reject column-flow regions that a text-only detector mistakes for tables."""
    if bbox.width < page_rect.width * 0.30 or bbox.height < 36:
        return False
    # Drawn grids are strong evidence and may legitimately fill a page. Text and
    # borderless strategies need a stricter cap because multi-column prose often
    # appears as one nearly full-page pseudo-table.
    if detection_source != "lines" and bbox.height > page_rect.height * 0.55:
        return False
    return True


def extract_instructional_pdf_tables(file_path: str, max_tables: int | None = None) -> list[dict]:
    """Render detected tables as images so teachers can describe them accessibly."""
    maximum = max_tables or int(os.getenv("MAX_PDF_TABLE_IMAGES", "6"))
    table_images = []
    document = fitz.open(file_path)
    try:
        for page_index, page in enumerate(document, start=1):
            page_rect = fitz.Rect(page.rect)
            detected = []
            table_candidates = []
            for strategy in ("lines", "text"):
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        candidates = page.find_tables(strategy=strategy).tables
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    candidates = []
                for table in candidates:
                    table_candidates.append(
                        {"rows": table.extract(), "bbox": fitz.Rect(table.bbox), "source": strategy}
                    )
            table_candidates.extend(
                {**candidate, "source": "borderless"}
                for candidate in _borderless_table_regions(page.get_text("dict"), page_rect)
            )

            for candidate in table_candidates:
                rows = candidate["rows"]
                bbox = fitz.Rect(candidate["bbox"])
                if not _instructional_table_rows(rows):
                    continue
                if not _plausible_table_bbox(bbox, page_rect, candidate.get("source") or "text"):
                    continue
                if any(
                    (bbox & existing).get_area()
                    / max(min(bbox.get_area(), existing.get_area()), 1)
                    >= 0.80
                    for existing in detected
                ):
                    continue
                detected.append(bbox)
                padding = 5.0
                crop_bbox = fitz.Rect(
                    max(page_rect.x0, bbox.x0 - padding),
                    max(page_rect.y0, bbox.y0 - padding),
                    min(page_rect.x1, bbox.x1 + padding),
                    min(page_rect.y1, bbox.y1 + padding),
                )
                try:
                    image_bytes = page.get_pixmap(clip=crop_bbox, dpi=180, alpha=False).tobytes("png")
                except (RuntimeError, ValueError):
                    continue
                visible_text = "\n".join(
                    " | ".join(re.sub(r"\s+", " ", str(cell or "")).strip() for cell in row)
                    for row in rows
                ).strip()
                title = _nearest_table_title(page, bbox, page_rect.height)
                if not title and page_index > 1 and bbox.y0 <= page_rect.height * 0.20:
                    title = _last_heading_on_page(document[page_index - 2])
                table_images.append(
                    {
                        "page_number": page_index,
                        "index": len(table_images),
                        "block_index": None,
                        "width": int(crop_bbox.width),
                        "height": int(crop_bbox.height),
                        "area": int(crop_bbox.get_area()),
                        "extension": "png",
                        "image_bytes": image_bytes,
                        "bbox": tuple(float(value) for value in bbox),
                        "title": title or f"Table on page {page_index}",
                        "visible_text": visible_text,
                        "caption": "",
                        "is_table": True,
                    }
                )
                if len(table_images) >= maximum:
                    return table_images
    finally:
        document.close()
    return table_images


def exclude_text_blocks_inside_tables(blocks: list[dict], tables: list[dict]) -> list[dict]:
    """Prevent table cells from also becoming flattened text learning objects."""
    regions_by_page: dict[int, list[fitz.Rect]] = {}
    for table in tables:
        bbox = table.get("bbox")
        page_number = table.get("page_number")
        if bbox and page_number:
            regions_by_page.setdefault(int(page_number), []).append(fitz.Rect(bbox))

    kept = []
    for block in blocks:
        bbox = block.get("bbox")
        if not bbox:
            kept.append(block)
            continue
        rect = fitz.Rect(bbox)
        center = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
        if any(region.contains(center) for region in regions_by_page.get(int(block.get("page") or 0), [])):
            continue
        kept.append(block)
    return kept


def _remove_images_overlapping_tables(images: list[dict], tables: list[dict]) -> list[dict]:
    kept = []
    for image in images:
        image_bbox = image.get("bbox")
        if not image_bbox:
            kept.append(image)
            continue
        image_rect = fitz.Rect(image_bbox)
        overlaps_table = False
        for table in tables:
            if table.get("page_number") != image.get("page_number") or not table.get("bbox"):
                continue
            table_rect = fitz.Rect(table["bbox"])
            intersection = image_rect & table_rect
            if intersection.get_area() / max(min(image_rect.get_area(), table_rect.get_area()), 1) >= 0.50:
                overlaps_table = True
                break
        if not overlaps_table:
            kept.append(image)
    return kept


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


def _rank_outline_nodes_by_tfidf(
    course: CourseGroup,
    title: str,
    text: str,
) -> list[tuple[OutlineNode, float]]:
    """Return deterministic topic candidates and their cosine scores."""
    nodes = list(course.nodes.all().order_by("depth", "order", "id"))
    if not nodes:
        return []

    material_text = clean_pdf_text_for_extraction(f"{title}\n{text}", limit=16000)
    if not material_text.strip():
        return []

    node_documents = [_outline_node_similarity_text(node) for node in nodes]
    if not any(document.strip() for document in node_documents):
        return []

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
        return []

    scores = cosine_similarity(matrix[0:1], matrix[1:]).flatten()
    return sorted(
        [(node, float(score)) for node, score in zip(nodes, scores)],
        key=lambda item: item[1],
        reverse=True,
    )


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
    ranked = _rank_outline_nodes_by_tfidf(course, title, text)
    if not ranked:
        return None

    best_node, best_score = ranked[0]
    broad_match_count = sum(
        score >= best_score * TFIDF_BROAD_MATCH_RATIO
        for _, score in ranked
    )

    if (
        best_score < TFIDF_COSINE_THRESHOLD
        or broad_match_count > TFIDF_MAX_BROAD_MATCHES
    ):
        return None

    return best_node


def choose_outline_node_for_material(course: CourseGroup, title: str, text: str) -> OutlineNode | None:
    """Public retrieval wrapper used by the generation pipeline.

    This exposes the deterministic document matching API and preserves the
    public contract expected by the rest of the lessons generation stack.
    """
    return _choose_outline_node_by_tfidf(course, title, text)


def validate_outline_node_for_material(
    course: CourseGroup,
    selected_node: OutlineNode | None,
    title: str,
    text: str,
) -> OutlineNode | None:
    """Require real PDF evidence without demanding one infallible top-ranked node."""
    if selected_node is None:
        return choose_outline_node_for_material(course, title, text)
    if selected_node.course_id != course.id:
        return None

    # Ignore the filename so a misleading name cannot manufacture support. A
    # teacher-selected topic is accepted when its own text score is meaningful
    # and reasonably close to the best sibling/related candidate. This avoids
    # rejecting lessons that cover several legitimate neighboring subtopics.
    ranked = _rank_outline_nodes_by_tfidf(course, "", text)
    if not ranked:
        return None
    best_score = ranked[0][1]
    selected_score = next(
        (score for node, score in ranked if node.pk == selected_node.pk),
        0.0,
    )
    if selected_score < SELECTED_TOPIC_COSINE_THRESHOLD:
        return None
    if best_score > 0 and selected_score < best_score * SELECTED_TOPIC_BEST_SCORE_RATIO:
        return None
    return selected_node


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
                "title": image.get("title") or "",
                "bbox": image.get("bbox"),
                "is_table": bool(image.get("is_table")),
                "source": "table_pdf" if image.get("is_table") else "image_pdf",
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


def _is_standalone_bullet_marker(text: str) -> bool:
    """Identify any non-text PDF marker emitted as its own line."""
    marker = (text or "").strip()
    if not marker:
        return False
    return all(
        character.isspace()
        or unicodedata.category(character)[0] in {"P", "S", "C"}
        for character in marker
    )


def _strip_leading_bullet_marker_lines(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    while len(lines) > 1 and _is_standalone_bullet_marker(lines[0]):
        lines.pop(0)
    return "\n".join(lines).strip()


def _inline_definition_split(text: str) -> tuple[str, str] | None:
    text = _strip_leading_bullet_marker_lines(text)
    text = re.sub(r"\s+", " ", text or "").strip()
    first_token, separator, remainder = text.partition(" ")
    if separator and _is_standalone_bullet_marker(first_token):
        text = remainder.strip()
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


def _multiline_definition_split(text: str) -> tuple[str, str] | None:
    """Recognize two-column vocabulary rows preserved as PyMuPDF lines."""
    if _is_symbolic_relation_text(text):
        return None
    text = _strip_leading_bullet_marker_lines(text)
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    title = lines[0].strip(" .:-–—")
    content = "\n".join(lines[1:]).lstrip("\x07•●▪ ").strip()
    if not title or not content or len(title.split()) > 7 or len(content.split()) < 3:
        return None
    if re.search(r"[.!?]$", title) or _is_question_or_activity_text(title):
        return None
    if _is_admin_or_system_support_text(title):
        return None
    return title[:255], content


def _definition_split(text: str) -> tuple[str, str] | None:
    return _inline_definition_split(text) or _multiline_definition_split(text)


def _followed_by_another_inline_definition(blocks: list[dict], index: int) -> bool:
    """Return whether this labeled line begins a group of sibling definitions."""
    for next_block in blocks[index + 1 :]:
        next_text = (next_block.get("text") or "").strip()
        if not next_text or _is_image_caption(next_text):
            continue
        if is_structural_metadata_label(next_text) or _starts_excluded_section(next_text):
            return False
        if next_block.get("category") not in {"lesson_content", "needs_review"}:
            return False
        return _definition_split(next_text) is not None
    return False


def _split_embedded_heading_blocks(blocks: list[dict]) -> list[dict]:
    """Separate an all-caps heading merged with body lines in one PDF block."""
    expanded = []
    for block in blocks:
        original_text = block.get("text") or ""
        normalized_text = _strip_leading_bullet_marker_lines(original_text)
        original_lines = [line.strip() for line in original_text.splitlines() if line.strip()]
        block = {
            **block,
            "text": normalized_text,
            "is_bullet_item": bool(
                original_lines
                and (
                    _is_standalone_bullet_marker(original_lines[0])
                    or re.match(r"^\s*[\u2022\u25cf\u25aa\-*]\s*\S", original_text)
                )
            ),
        }
        lines = [line.strip() for line in normalized_text.splitlines() if line.strip()]
        first_line = lines[0] if lines else ""
        letters = re.sub(r"[^A-Za-z]+", "", first_line)
        heading_like = (
            len(lines) >= 2
            and bool(letters)
            and letters.isupper()
            and len(first_line.split()) <= 8
            and len(" ".join(lines[1:]).split()) >= 5
            and not _is_question_or_activity_text(first_line)
        )
        if not heading_like:
            expanded.append(block)
            continue

        expanded.append({**block, "text": first_line, "line_count": 1})
        expanded.append(
            {
                **block,
                "text": "\n".join(lines[1:]).lstrip("\x07•●▪ ").strip(),
                "line_count": max(len(lines) - 1, 1),
            }
        )
    return expanded


def _section_heading_title(text: str) -> str | None:
    match = re.fullmatch(r"\s*\d+(?:\.\d+)*\.?\s+(.+?)\s*", text or "")
    if not match:
        return None
    title = match.group(1).strip(" .:")
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
        return text.strip(" .:")[:255]
    return None


def _is_supporting_component_heading(text: str) -> bool:
    """Return whether a heading supplies evidence for the active concept."""
    label = _normalized_heading_label(text)
    return bool(
        re.fullmatch(
            r"(?:definitions?|explanations?|examples?)(?:\s+(?:of|for|in)\s+.+)?",
            label,
        )
    )


def _is_symbolic_relation_text(text: str) -> bool:
    """Recognize compact authored relationships without knowing subject terms."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    return bool(
        normalized
        and len(normalized) <= 180
        and re.search(r"\S\s*(?:\u2192|->|=>)\s*\S", normalized)
    )


def _current_concept_accepts_supporting_component(current: dict, heading: str) -> bool:
    if not current or not _is_supporting_component_heading(heading):
        return False
    parts = current.get("parts") or []
    if not parts or current.get("has_supporting_components") or len(parts) >= 2:
        return True
    existing_content = " ".join(parts)
    return bool(
        _is_symbolic_relation_text(existing_content)
        or (
            len((current.get("title") or "").split()) <= 3
            and (
                re.search(r"[.!?]$", existing_content)
                or re.search(
                    r"\b(?:is|are)\s+(?:a|an|the)\b|\bis defined as\b|\brefers? to\b",
                    existing_content,
                    flags=re.IGNORECASE,
                )
            )
        )
    )


def _enumerated_item_number(text: str) -> int | None:
    match = re.match(r"^\s*(\d+)[.)]\s+\S", text or "")
    if not match or _is_question_or_activity_text(text):
        return None
    return int(match.group(1))


def _is_discourse_continuation_label(text: str) -> bool:
    """Recognize transition labels that continue a thought rather than name it."""
    label = _normalized_heading_label(text)
    return bool(
        str(text or "").rstrip().endswith(":")
        and label
        in {
            "afterward",
            "consequently",
            "eventually",
            "finally",
            "first",
            "however",
            "meanwhile",
            "next",
            "then",
            "therefore",
        }
    )


def _heading_refers_to_current_concept(current: dict, heading: str) -> bool:
    """Use lexical overlap to keep concept-specific subheadings with a concept."""
    ignored = {
        "a",
        "an",
        "and",
        "chapter",
        "example",
        "examples",
        "for",
        "in",
        "lesson",
        "of",
        "part",
        "section",
        "the",
        "to",
        "unit",
    }
    title_words = [
        word[:-1] if word.endswith("s") and len(word) > 3 else word
        for word in _normalized_heading_label(current.get("title") or "").split()
        if word not in ignored and not word.isdigit()
    ]
    heading_words = {
        word[:-1] if word.endswith("s") and len(word) > 3 else word
        for word in _normalized_heading_label(heading).split()
        if word not in ignored and not word.isdigit()
    }
    return bool(title_words and len(title_words) <= 3 and set(title_words) & heading_words)


def _raw_learning_object_heading_title(block: dict) -> str | None:
    if block.get("teacher_override") or block.get("is_bullet_item"):
        return None
    if block.get("category") in {
        "answer_key",
        "assessment",
        "decorative_or_noise",
        "navigation",
        "reference",
        "teacher_note",
    }:
        return None
    if (
        block.get("category") == "document_metadata"
        and block.get("reason") != "Short heading or label rather than narration."
    ):
        return None
    text = block.get("text", "")
    if _is_symbolic_relation_text(text):
        return None
    if is_structural_metadata_label(text):
        return None
    if int(block.get("line_count") or 1) >= 2 and not _section_heading_title(text):
        return None
    return _section_heading_title(text) or _looks_like_plain_subtopic_heading(text)


def _learning_object_heading_title(block: dict) -> str | None:
    if block.get("teacher_override") or block.get("is_bullet_item"):
        return None
    if _is_symbolic_relation_text(block.get("text", "")):
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
    if label == "materials":
        return True
    excluded_section_labels = {
        "about the author",
        "about the authors",
        "acknowledgment",
        "acknowledgments",
        "acknowledgement",
        "acknowledgements",
        "answer key",
        "answers",
        "bibliography",
        "copyright",
        "dedication",
        "general instructions",
        "glossary",
        "index",
        "learning objective",
        "learning objectives",
        "objective",
        "objectives",
        "essential question",
        "essential questions",
        "success criteria",
        "learning goal",
        "learning goals",
        "chapter overview",
        "course overview",
        "lesson overview",
        "module overview",
        "unit overview",
        "lesson objective",
        "lesson objectives",
        "teacher notes",
        "teacher note",
        "publisher information",
        "references",
        "sources",
        "suggested answers",
        "table of contents",
        "activity",
        "background and misconceptions",
        "additional topics",
        "cut or fold",
        "extension activities",
        "prior knowledge",
        "other topics",
        "probing questions to think about",
        "projects and activities",
        "spark",
        "unit materials",
        "using the internet",
        "vocabulary",
        "vocabulary activities",
        "word sort",
        "word work",
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

    return False


def _excluded_section_allows_prose_exit(text: str) -> bool:
    """Objectives may be followed directly by lesson prose without a heading.

    Teacher-guide, worksheet, assessment, and reference sections deliberately
    require a real heading before content can resume.
    """
    label = _normalized_heading_label(text)
    return label in {
        "learning objective",
        "learning objectives",
        "objective",
        "objectives",
        "lesson objective",
        "lesson objectives",
    }


def _finalize_current_learning_object(current: dict | None, learning_objects: list[dict]) -> None:
    if not current:
        return
    parts = current.pop("parts", [])
    current.pop("has_supporting_components", None)
    current.pop("expected_enumerated_item", None)
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
        "answer_key",
        "document_metadata",
        "learning_objective",
        "assessment",
        "navigation",
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

    # Exclude prose about how lesson content should be written or formatted;
    # it describes the document rather than teaching its subject.
    authoring_terms = {"write", "written", "read", "format", "notes", "sequence", "collection"}
    document_terms = {"lesson", "content", "example", "examples", "question", "questions", "facts"}
    if {"you", "want"}.issubset(set(label.split())):
        tokens = set(label.split())
        if tokens & authoring_terms and len(tokens & document_terms) >= 2:
            return True

    tokens = set(label.split())
    editorial_terms = {"actual", "like", "more", "random", "rather", "should", "want"}
    editorial_content_terms = {"content", "document", "format", "info", "information", "lesson", "notes", "read"}
    if (
        "teacher" in tokens
        and "notes" in tokens
        and tokens & editorial_terms
        and tokens & editorial_content_terms
    ):
        return True

    support_terms = {"teacher", "algorithm", "edge", "scoring", "verify"}
    path_terms = {"learner", "learning", "path", "prerequisite", "dependency", "concept", "node", "nodes"}
    return bool(tokens & support_terms) and len(tokens & path_terms) >= 2


def _is_question_or_activity_text(text: str) -> bool:
    stripped = re.sub(r"\s+", " ", text or "").strip()
    label = _normalized_heading_label(stripped)
    if not label:
        return False
    if re.match(r"^\s*[qa]\s*:", stripped, flags=re.IGNORECASE):
        return True

    section_labels = {
        "answer",
        "answer key",
        "answers",
        "expected answer",
        "expected answers",
        "suggested answer",
        "suggested answers",
        "sample answer",
        "sample answers",
        "correct answer",
        "correct answers",
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
        "teacher check",
        "knowledge check",
        "comprehension check",
        "self check",
        "ask",
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
    if re.search(r"_{3,}", stripped):
        return True
    if len(re.findall(r"(?:^|\s)[A-Da-d][.)]\s+", stripped)) >= 2:
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
    if re.match(r"^\s*(?:directions?|instructions?)\s*:", stripped, flags=re.IGNORECASE):
        return True
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
    # An uncertain block may be attached to an already validated concept
    # heading by the stateful builder, but it cannot create a standalone
    # learning object by itself.
    if block.get("category") == "needs_review":
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
        if _is_supporting_component_heading(text):
            continue
        if _is_symbolic_relation_text(text):
            return True
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


def _is_short_wrapped_continuation(item: dict, text: str, block: dict) -> bool:
    """Accept a short spillover block only when the prior sentence is unfinished."""
    existing = (item.get("content") or "").rstrip()
    text = (text or "").strip()
    if item.get("type") != "lesson_content" or not existing or not text:
        return False
    if re.search(r"[.!?;:]$", existing) or len(text.split()) > 12:
        return False
    if _is_question_or_activity_text(text) or _is_admin_or_system_support_text(text):
        return False
    if item.get("source_page") and block.get("page") and item["source_page"] != block["page"]:
        return False
    return bool(re.match(r"^[a-z(]", text))


def build_section_learning_objects(classified_blocks: list[dict], image_descriptions: list[dict]) -> list[dict]:
    classified_blocks = _split_embedded_heading_blocks(classified_blocks)
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
        provided_title = (image.get("title") or "").strip()
        if provided_title:
            title = provided_title[:255]
        elif description:
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
                "is_table": bool(image.get("is_table")),
                "source_y": float((image.get("bbox") or (0, 0, 0, 0))[1]),
            }
        )

    current = None
    active_section_title = ""
    skipping_excluded_section = False
    excluded_section_allows_prose_exit = False
    can_append_to_previous = True
    for index, block in enumerate(classified_blocks):
        text = block.get("text", "").strip()
        if not text or _is_image_caption(text):
            continue

        if is_structural_metadata_label(text):
            _finalize_current_learning_object(current, learning_objects)
            current = None
            active_section_title = ""
            can_append_to_previous = False
            continue

        # A rhetorical question can be an authored bridge to an explanation.
        # Keep the explanation attached to the active concept, but never retain
        # the question itself as a learning object. Assessment sections such as
        # "Teacher Check" are handled by the excluded-section state instead.
        if (
            not skipping_excluded_section
            and block.get("category") == "assessment"
            and text.rstrip().endswith("?")
            and _heading_has_following_content(classified_blocks, index)
        ):
            continue

        if _starts_excluded_section(text):
            _finalize_current_learning_object(current, learning_objects)
            current = None
            active_section_title = ""
            skipping_excluded_section = True
            excluded_section_allows_prose_exit = _excluded_section_allows_prose_exit(text)
            can_append_to_previous = False
            continue
        if skipping_excluded_section:
            prose_exit = (
                excluded_section_allows_prose_exit
                and block.get("category") == "lesson_content"
                and int(block.get("line_count") or 1) >= 2
                and len(text.split()) >= 5
                and not _is_learning_objective_statement(text)
            )
            if _ends_excluded_section(block, classified_blocks, index) or prose_exit:
                skipping_excluded_section = False
                if _starts_excluded_section(text):
                    skipping_excluded_section = True
                    excluded_section_allows_prose_exit = _excluded_section_allows_prose_exit(text)
                    continue
            else:
                continue

        item_number = _enumerated_item_number(text)
        expected_item_number = current.get("expected_enumerated_item") if current else None
        starts_enumerated_list = bool(
            current
            and current.get("parts")
            and str(current["parts"][-1]).rstrip().endswith(":")
        )
        if current and item_number is not None and (
            (starts_enumerated_list and item_number == 1) or item_number == expected_item_number
        ):
            current["parts"].append(text)
            current["expected_enumerated_item"] = item_number + 1
            if not current.get("source_excerpt"):
                current["source_excerpt"] = text
            continue
        if current and expected_item_number is not None:
            current.pop("expected_enumerated_item", None)

        if current is not None and _is_discourse_continuation_label(text):
            current["parts"].append(text)
            continue

        inline_definition = None
        if block.get("category") in {"lesson_content", "needs_review"}:
            inline_definition = _definition_split(text)
        if inline_definition:
            title, content = inline_definition
            if current is not None and _current_concept_accepts_supporting_component(current, title):
                current["parts"].append(f"{title}:\n{content}")
                current["has_supporting_components"] = True
                if not current.get("source_excerpt"):
                    current["source_excerpt"] = text
                can_append_to_previous = True
                continue
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
            inline_item = {
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
            if followed_by_sibling or active_section_title:
                learning_objects.append(inline_item)
            else:
                current = {**inline_item, "content": "", "parts": [content]}
                can_append_to_previous = True
            continue

        heading_title = _learning_object_heading_title(block)
        if not heading_title:
            raw_heading_title = _raw_learning_object_heading_title(block)
            if raw_heading_title and _section_heading_title(text) and _heading_has_following_content(classified_blocks, index):
                heading_title = raw_heading_title
            elif raw_heading_title and _plain_heading_has_following_body_content(classified_blocks, index):
                heading_title = raw_heading_title
        if heading_title:
            if current is not None and (
                _current_concept_accepts_supporting_component(current, heading_title)
                or (
                    not _section_heading_title(text)
                    and _heading_refers_to_current_concept(current, heading_title)
                )
            ):
                current["parts"].append(f"{heading_title}:")
                current["has_supporting_components"] = True
                can_append_to_previous = True
                continue
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
            can_append_to_previous = True
            continue

        keep_as_content = _block_is_kept_content(block)

        if not keep_as_content:
            if block.get("category") in {"assessment", "teacher_note", "concept_metadata", "table_header", "reference"}:
                _finalize_current_learning_object(current, learning_objects)
                current = None
                can_append_to_previous = False
            elif (
                block.get("category") == "needs_review"
                and current is None
                and can_append_to_previous
                and learning_objects
                and _is_short_wrapped_continuation(learning_objects[-1], text, block)
            ):
                _append_pdf_text_to_learning_object(learning_objects[-1], text, block)
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
            if can_append_to_previous and has_section_headings and learning_objects:
                if _append_pdf_text_to_learning_object(learning_objects[-1], text, block):
                    continue

            if not can_append_to_previous and has_section_headings:
                continue

            # A genuinely unheaded document still needs one usable card. Its
            # title is copied from its own first sentence, never generated.
            unheaded_item = {
                "order": len(learning_objects),
                "title": _title_from_teacher_text(text),
                "type": "lesson_content",
                "content": text,
                "source": "teacher_pdf",
                "source_page": block.get("page"),
                "source_block_id": block.get("block_id"),
                "source_excerpt": text,
            }
            if has_section_headings:
                current = {**unheaded_item, "content": "", "parts": [text]}
                can_append_to_previous = True
            else:
                learning_objects.append(unheaded_item)
            continue
        if current.get("title", "").casefold() == text.casefold():
            continue
        current["parts"].append(text)
        if not current.get("source_excerpt"):
            current["source_excerpt"] = text

    _finalize_current_learning_object(current, learning_objects)

    block_positions = {
        block.get("block_id"): float((block.get("bbox") or (0, 0, 0, 0))[1])
        for block in classified_blocks
        if block.get("block_id") is not None
    }
    for item in learning_objects:
        if item.get("source_y") is None:
            item["source_y"] = block_positions.get(item.get("source_block_id"), float("inf"))
    learning_objects.sort(
        key=lambda item: (
            int(item.get("source_page") or 10**9),
            float(item.get("source_y") if item.get("source_y") is not None else float("inf")),
            int(item.get("order") or 0),
        )
    )
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


def _learning_object_word_count(item: dict) -> int:
    return len(re.findall(r"\b[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)?\b", item.get("content") or ""))


def _learning_object_units(content: str) -> list[str]:
    """Reconstruct visual wraps while retaining authored list boundaries."""
    paragraphs = []
    current_lines = []
    for raw_line in (content or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            if current_lines:
                paragraphs.append(" ".join(current_lines))
                current_lines = []
            continue

        if current_lines:
            previous_line = current_lines[-1]
            next_starts_new_idea = bool(re.match(r"^[A-Z0-9]", line))
            previous_is_short_label = (
                len(previous_line.split()) <= 8
                and not re.search(r"[,\-]$", previous_line)
            )
            if next_starts_new_idea and (
                previous_is_short_label
                or previous_line.endswith(":")
                or _is_symbolic_relation_text(previous_line)
            ):
                paragraphs.append(" ".join(current_lines))
                current_lines = []

        current_lines.append(line)
        if (
            re.search(r"[.!?][\"')\]]?$", line)
            or line.endswith(":")
            or _is_symbolic_relation_text(line)
            or _enumerated_item_number(line) is not None
        ):
            paragraphs.append(" ".join(current_lines))
            current_lines = []

    if current_lines:
        paragraphs.append(" ".join(current_lines))

    units = []
    for paragraph in paragraphs:
        sentences = re.split(r"(?<!\d\.)(?<=[.!?])\s+(?=[A-Z0-9])", paragraph)
        units.extend(sentence.strip() for sentence in sentences if sentence.strip())
    return units


def _split_oversized_learning_object(item: dict, maximum_words: int) -> list[dict]:
    if item.get("type") != "lesson_content" or _learning_object_word_count(item) <= maximum_words:
        return [item]

    units = _learning_object_units(item.get("content") or "")
    if len(units) < 2:
        return [item]

    total_words = sum(len(re.findall(r"\b\w+\b", unit)) for unit in units)
    group_count = max(2, (total_words + maximum_words - 1) // maximum_words)
    target_words = max(1, (total_words + group_count - 1) // group_count)
    groups: list[list[str]] = []
    current: list[str] = []
    current_words = 0
    for unit in units:
        unit_words = len(re.findall(r"\b\w+\b", unit))
        groups_still_needed = group_count - len(groups)
        if (
            current
            and current_words >= target_words
            and groups_still_needed > 1
            and not current[-1].rstrip().endswith(":")
        ):
            groups.append(current)
            current = []
            current_words = 0
        current.append(unit)
        current_words += unit_words
    if current:
        groups.append(current)

    if len(groups) < 2:
        return [item]

    title = (item.get("title") or "Learning object").strip()
    split_items = []
    for index, group in enumerate(groups, start=1):
        content = "\n".join(group)
        split_items.append(
            {
                **item,
                "title": f"{title} (Part {index} of {len(groups)})"[:255],
                "content": content,
                "source_excerpt": content,
                "chunk_index": index,
                "chunk_count": len(groups),
                "chunk_operation": "split_at_authored_boundaries",
            }
        )
    return split_items


def _is_relation_micro_object(item: dict) -> bool:
    content = (item.get("content") or "").strip()
    return "\n" not in content and _is_symbolic_relation_text(content)


def _adjacent_learning_object_similarity(left: dict, right: dict) -> float:
    documents = [
        f"{left.get('title', '')} {left.get('content', '')}",
        f"{right.get('title', '')} {right.get('content', '')}",
    ]
    try:
        matrix = TfidfVectorizer(lowercase=True, stop_words="english", ngram_range=(1, 2)).fit_transform(documents)
    except (TypeError, ValueError):
        return 0.0
    return float(cosine_similarity(matrix[0:1], matrix[1:2])[0, 0])


def _is_protected_outline_object(item: dict, outline_context: dict) -> bool:
    protected_titles = {
        str(outline_context.get("topic_title") or ""),
        str(outline_context.get("module_title") or ""),
        *(str(title) for title in (outline_context.get("sibling_topics") or [])),
    }
    return any(title and _is_same_label(item.get("title") or "", title) for title in protected_titles)


def _can_merge_short_learning_objects(
    left: dict,
    right: dict,
    minimum_words: int,
    similarity_threshold: float,
    outline_context: dict,
) -> bool:
    if left.get("type") != "lesson_content" or right.get("type") != "lesson_content":
        return False
    if not left.get("section_title") or left.get("section_title") != right.get("section_title"):
        return False
    if _learning_object_word_count(left) >= minimum_words or _learning_object_word_count(right) >= minimum_words:
        return False
    if _is_protected_outline_object(left, outline_context) or _is_protected_outline_object(right, outline_context):
        return False
    if _is_relation_micro_object(left) and _is_relation_micro_object(right):
        return True
    return _adjacent_learning_object_similarity(left, right) >= similarity_threshold


def _merge_learning_object_group(group: list[dict]) -> dict:
    section_title = (group[0].get("section_title") or "").strip()
    first_title = (group[0].get("title") or "Concept").strip()
    last_title = (group[-1].get("title") or "Concept").strip()
    merged_title = section_title or f"{first_title} to {last_title}"
    merged_content = "\n".join(
        f"{(item.get('title') or '').strip()}: {(item.get('content') or '').strip()}".strip(": ")
        for item in group
    )
    source_pages = list(dict.fromkeys(item.get("source_page") for item in group if item.get("source_page")))
    source_block_ids = list(
        dict.fromkeys(item.get("source_block_id") for item in group if item.get("source_block_id"))
    )
    return {
        **group[0],
        "section_title": "" if section_title else group[0].get("section_title", ""),
        "title": merged_title[:255],
        "content": merged_content,
        "source_excerpt": merged_content,
        "source_pages": source_pages,
        "source_block_ids": source_block_ids,
        "chunk_count": len(group),
        "chunk_operation": "adjacent_short_object_merge",
    }


def balance_learning_object_chunks(
    learning_objects: list[dict],
    outline_context: dict | None = None,
) -> list[dict]:
    """Balance object size using authored boundaries and adjacent lexical cohesion."""
    outline_context = outline_context or {}
    minimum_words = max(1, int(os.getenv("LEARNING_OBJECT_MIN_WORDS", "8")))
    maximum_words = max(minimum_words + 1, int(os.getenv("LEARNING_OBJECT_MAX_WORDS", "60")))
    similarity_threshold = min(
        1.0,
        max(0.0, float(os.getenv("LEARNING_OBJECT_MERGE_COSINE_THRESHOLD", "0.32"))),
    )

    split_objects = []
    for item in learning_objects:
        split_objects.extend(_split_oversized_learning_object(item, maximum_words))

    balanced = []
    index = 0
    while index < len(split_objects):
        group = [split_objects[index]]
        next_index = index + 1
        while next_index < len(split_objects) and _can_merge_short_learning_objects(
            group[-1],
            split_objects[next_index],
            minimum_words,
            similarity_threshold,
            outline_context,
        ):
            group.append(split_objects[next_index])
            next_index += 1
        balanced.append(_merge_learning_object_group(group) if len(group) > 1 else group[0])
        index = next_index

    for order, item in enumerate(balanced):
        item["order"] = order
    return balanced


def _accessible_narration_text(text: str) -> str:
    """Verbalize common visual relationship symbols for screenless listening."""
    spoken = str(text or "")
    replacements = (
        (r"\s*(?:\u2192|->|=>)\s*", " to "),
        (r"\s*\u2264\s*", " less than or equal to "),
        (r"\s*\u2265\s*", " greater than or equal to "),
        (r"\s*\u2260\s*", " is not equal to "),
    )
    for pattern, replacement in replacements:
        spoken = re.sub(pattern, replacement, spoken)
    spoken = re.sub(r"[ \t]+", " ", spoken).strip()
    # Glossaries commonly omit punctuation after short definitions. Preserve
    # the extracted wording in the learning object, but close the spoken form
    # so TTS does not run it into the next playlist item.
    if spoken and not re.search(r"[.!?][\"')\]]?$", spoken):
        spoken += "."
    return spoken


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
        elif title and not _is_same_label(title, content):
            content = f"{title}. {content}".strip()
        content = _accessible_narration_text(content)
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
    """Remove non-learner fragments and questions before chunking/narration.

    This is a deterministic final guard. It does not invent or summarize text;
    it only removes lines that the structural classifier already recognizes as
    teacher-facing, administrative, assessment, or activity content.
    """
    reviewed = []
    for original in learning_objects:
        item = {**original}
        is_image = item.get("type") in {"image_description", "image"} or bool(item.get("image_url"))
        if is_image:
            reviewed.append(item)
            continue

        title = (item.get("title") or "").strip()
        content = (item.get("content") or "").strip()
        if not content:
            continue
        if _is_admin_or_system_support_text(title) or _is_question_or_activity_text(title):
            continue

        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if _is_admin_or_system_support_text(content):
            # A layout block may join a valid relation to a teacher comment.
            # Retain only independently meaningful authored relationships.
            kept_lines = [line for line in lines if _is_symbolic_relation_text(line)]
        else:
            kept_lines = [
                line
                for line in lines
                if not _is_admin_or_system_support_text(line)
                and not _is_question_or_activity_text(line)
            ]

        cleaned_content = "\n".join(kept_lines).strip()
        if not cleaned_content:
            continue
        if cleaned_content != content:
            item["content"] = cleaned_content
            item["accessibility_cleanup"] = "removed_nonlearner_text"
        reviewed.append(item)

    for order, item in enumerate(reviewed):
        item["order"] = order
    return reviewed


def json_dumps_for_prompt(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)


def _sync_learning_objects(material: LearningMaterial, generated_json: dict):
    from .learning_resource_linker import (
        prior_learning_object_groups,
        question_snapshots,
        refresh_learning_object_match_suggestions,
        remove_empty_learning_object_groups,
        resolve_learning_object_group,
        synchronize_detected_questions,
    )

    if not _material_exists(material):
        raise MaterialDeletedDuringGeneration(
            f"Learning material {material.pk} was deleted before learning objects were saved."
        )
    prior_groups = prior_learning_object_groups(material)
    material.learning_objects.all().delete()
    synced_learning_objects = []
    for index, item in enumerate(generated_json.get("learning_objects", [])):
        if is_structural_metadata_label(item.get("title") or ""):
            continue
        is_image = item.get("type") in {"image_description", "image"} or bool(item.get("image_url"))
        kind = LearningObject.Kind.IMAGE if is_image else LearningObject.Kind.TEXT
        title = (
            item.get("title")
            or _title_from_learning_object_item(
                item,
                "Extracted image" if is_image else "Untitled content",
            )
        )[:255]
        group = resolve_learning_object_group(
            material,
            title,
            kind,
            index,
            content=item.get("content") or "",
            section_title=item.get("section_title") or "",
            prior_groups=prior_groups,
        )
        learning_object = LearningObject.objects.create(
            material=material,
            group=group,
            kind=kind,
            section_title=(item.get("section_title") or "")[:255],
            title=title,
            content=item.get("content") or "",
            image_url=item.get("image_url") or "",
            source_page=item.get("source_page"),
            source_block_id=item.get("source_block_id"),
            source_excerpt=item.get("source_excerpt") or "",
            order=index,
        )
        synced_learning_objects.append(
            {
                **item,
                "order": index,
                "kind": kind,
                "learning_object_id": learning_object.id,
                "learning_object_group_id": learning_object.group_id,
            }
        )
    generated_json["learning_objects"] = synced_learning_objects
    remove_empty_learning_object_groups(material)
    refresh_learning_object_match_suggestions(material)
    synchronize_detected_questions(material, generated_json.get("classified_blocks") or [])
    generated_json["questions"] = question_snapshots(material)
    material.generated_json = generated_json
    _save_material_update(material, ["generated_json"])


def rebuild_generated_outputs_from_classifications(generated_json: dict) -> dict:
    classified_blocks = generated_json.get("classified_blocks") or []
    image_descriptions = generated_json.get("image_descriptions") or []
    sections = split_classified_blocks(classified_blocks)
    learning_objects = remove_structural_metadata_learning_objects(
        build_section_learning_objects(classified_blocks, image_descriptions)
    )
    learning_objects = review_learning_objects_for_bvi_learners(learning_objects)
    learning_objects = balance_learning_object_chunks(
        learning_objects,
        generated_json.get("teacher_outline_context") or {},
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
        if is_course_outline_document(cleaned_preserved_text):
            raise ValueError(
                "This PDF appears to be a course outline, not lesson material. "
                "Upload it through Course outline extraction."
            )
        metadata_text = _limited_text(cleaned_preserved_text)
        extraction_mode = "deterministic_page_text_fallback" if is_image_only_pdf else "embedded_pdf_text"
        _trace(f"text extracted: {len(text)} chars, {len(extracted_blocks)} blocks, mode={extraction_mode}")

        selected_node = material.outline_node
        _trace("validating PDF against course outline")
        matched_node = validate_outline_node_for_material(
            material.course,
            selected_node,
            material.title,
            metadata_text,
        )
        if matched_node is None:
            if selected_node is not None:
                raise ValueError(
                    f'This PDF does not match the selected topic "{selected_node.title}" '
                    "or any sufficiently confident topic in the approved course outline."
                )
            raise ValueError(
                "This PDF does not match any topic in the approved course outline."
            )

        auto_classified = selected_node is None
        if auto_classified:
            material.outline_node = matched_node
            if material.module_node_id is None:
                module_node = matched_node
                while module_node.parent_id is not None:
                    module_node = module_node.parent
                material.module_node = module_node

        table_images = [] if is_image_only_pdf else extract_instructional_pdf_tables(material.pdf_file.path)
        if table_images:
            extracted_blocks = exclude_text_blocks_inside_tables(extracted_blocks, table_images)
        regular_images = [] if is_image_only_pdf else extract_meaningful_pdf_images(material.pdf_file.path)
        regular_images = _remove_images_overlapping_tables(regular_images, table_images)
        images = [*table_images, *regular_images]
        for image_index, image in enumerate(images):
            image["index"] = image_index
        if images and material.id:
            images = save_extracted_pdf_images(images, material.id)
        _trace(f"images extracted: {len(images)} ({len(table_images)} tables)")

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
        if not has_lesson_content and not learning_objects:
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
        learning_objects = review_learning_objects_for_bvi_learners(learning_objects)
        learning_objects = balance_learning_object_chunks(learning_objects, outline_context)
        narration_script = build_narration_script_from_learning_objects(learning_objects)
        playlist = build_lesson_playlist(narration_script)
        generated_json = {
            "generated_json_version": 4,
            "lesson_title": lesson_title,
            "suggested_lesson_title": metadata.get("lesson_title") or "",
            "teacher_outline_context": outline_context,
            "classification_method": (
                "tfidf_cosine_similarity"
                if auto_classified
                else "teacher_selected_topic_validated_by_tfidf"
            ),
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
