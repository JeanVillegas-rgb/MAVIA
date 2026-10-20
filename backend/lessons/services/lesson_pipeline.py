"""Course-material pipeline using deterministic extraction and Jean's rules."""

import logging

import fitz
import requests
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from lessons.models import LearningMaterial

from .block_classifier import classify_blocks, detect_instructional_document_role
from .audio_generator import build_lesson_playlist, build_narration_script_from_learning_objects
from .concept_grouper import group_material_learning_objects
from .embedding_service import cache_subtopic_embeddings, save_learning_objects
from .learning_object_builder import build_learning_objects
from .lesson_pdf_extractor import extract_lesson_pdf_blocks
from .question_workflow import question_snapshots, synchronize_detected_questions
from .topic_matcher import suggest_main_topic, suggest_main_topic_for_drafts
from .visual_describer import describe_visual_objects


logger = logging.getLogger(__name__)


class LessonPipelineError(ValueError):
    """The PDF content is not valid lesson material for this course."""


class LessonPipelineUnavailableError(RuntimeError):
    """A dependency failed while processing an otherwise readable PDF."""


def _mark_failed(material, exc):
    material.status = LearningMaterial.Status.FAILED
    material.error_message = str(exc)
    material.save(update_fields=["status", "error_message"])


def _store_visuals(material, visuals):
    stored, images_by_url = [], {}
    for visual in visuals:
        suffix = str(visual["block_index"]).replace("/", "_")
        filename = (
            f"lesson_images/material_{material.pk}/"
            f"page_{visual['page']}_block_{suffix}.png"
        )
        image_bytes = visual.pop("png_bytes", b"")
        stored_name = default_storage.save(filename, ContentFile(image_bytes))
        image_url = default_storage.url(stored_name)
        images_by_url[image_url] = image_bytes
        stored.append({**visual, "image_url": image_url})
    return stored, images_by_url


def _module_for(node):
    current = node
    while current and current.parent_id is not None:
        current = current.parent
    return current


def _draft_snapshot(draft, learning_object):
    return {
        **draft,
        "order": learning_object.order,
        "kind": learning_object.kind,
        "learning_object_id": learning_object.id,
        "learning_object_group_id": learning_object.group_id,
    }


def _teaching_blocks(classified):
    return [
        block for block in classified
        if block.get("category") == "needs_review"
        or block.get("include_in_narration") is True
    ]


def _describe_visuals_if_available(drafts, images_by_url):
    """Describe figures without making a temporary Groq outage reject the PDF."""
    if not drafts or not images_by_url:
        return ""
    try:
        describe_visual_objects(drafts, images_by_url)
    except requests.RequestException as exc:
        logger.warning(
            "[Upload] Image descriptions postponed because Groq is unavailable: %s",
            exc,
        )
        return "Image descriptions are pending because the AI service was unavailable."
    return ""


def generate_material_outputs(material, *, propagate_validation_error=False):
    """Run the complete lesson pipeline and store MAVIA-compatible records."""
    try:
        blocks, visuals = extract_lesson_pdf_blocks(material.pdf_file.path)
        if not blocks:
            raise LessonPipelineError(
                "No extractable lesson text was found. The PDF may require OCR."
            )
        classified = classify_blocks(blocks)
        extracted_text = "\n\n".join(block["text"] for block in blocks)
        stored_visuals, images_by_url = _store_visuals(material, visuals)
        drafts = build_learning_objects(_teaching_blocks(classified), stored_visuals)
        role = detect_instructional_document_role(classified)
        matching_drafts = drafts
        if role == "assessment":
            drafts = []
            matching_drafts = [
                {
                    "draft_id": f"assessment_{index}",
                    "title": "Assessment",
                    "section_title": "Assessment",
                    "content": block["text"],
                }
                for index, block in enumerate(classified, start=1)
                if block["category"] == "assessment"
            ]

        visual_warning = _describe_visuals_if_available(drafts, images_by_url)

        cache_subtopic_embeddings(material.course)
        rows = save_learning_objects(material, drafts)

        if material.outline_node_id is None:
            result = (
                suggest_main_topic(material)
                if drafts
                else suggest_main_topic_for_drafts(material.course, matching_drafts)
            )
            if result is None:
                raise LessonPipelineError(
                    "The PDF does not contain enough lesson content to match an outline topic."
                )
            material.outline_node = result["suggested_topic"]["outline_node"]
            material.module_node = _module_for(material.outline_node)

        from .source_review import source_review_is_finalized

        if source_review_is_finalized(material.outline_node):
            raise LessonPipelineError(
                "Lesson sources for this subtopic have been finalized. "
                "Additional PDFs cannot be uploaded."
            )

        saved_drafts = [
            _draft_snapshot(draft, row)
            for draft, row in zip(drafts, rows)
        ]
        if material.learning_objects.exists():
            group_material_learning_objects(material)
            groups = {
                row.id: row.group_id for row in material.learning_objects.all()
            }
            for draft in saved_drafts:
                draft["learning_object_group_id"] = groups.get(draft["learning_object_id"])

        synchronize_detected_questions(material, classified)

        narration = build_narration_script_from_learning_objects([
            {
                **draft,
                "source_block_id": (draft.get("source_block_ids") or [None])[0],
            }
            for draft in saved_drafts
        ])
        material.extracted_text = extracted_text
        material.extracted_blocks = classified
        material.page_count = max(
            [block["page"] for block in blocks]
            + [visual["page"] for visual in stored_visuals]
            + [0]
        )
        material.generated_json = {
            "generated_json_version": 5,
            "pipeline": "lesson_rules_v1",
            "lesson_title": (
                material.outline_node.title
                if material.outline_node_id
                else material.title
            ),
            "classification_method": "sbert_learning_object_average",
            "document_role": role,
            "original_text": extracted_text,
            "cleaned_preserved_text": extracted_text,
            "classified_blocks": classified,
            "learning_objects": saved_drafts,
            "narration_script": narration,
            "lesson_playlist": build_lesson_playlist(narration),
            "image_descriptions": [
                draft for draft in saved_drafts if draft.get("type") in {"image", "table"}
            ],
            "learning_objectives": [],
            "assessments": [
                block for block in classified if block["category"] == "assessment"
            ],
            "questions": question_snapshots(material),
            "teacher_notes": [],
            "concept_metadata_blocks": [],
            "ignored_blocks": [
                block for block in classified
                if block["category"] not in {"lesson_content", "assessment", "needs_review"}
            ],
            "processing_warnings": [visual_warning] if visual_warning else [],
            # Every extraction/regeneration requires teacher confirmation.
            "learning_objects_confirmed": False,
        }
        material.status = LearningMaterial.Status.COMPLETED
        material.error_message = ""
        material.save(update_fields=[
            "outline_node", "module_node", "extracted_text", "extracted_blocks",
            "page_count", "generated_json", "status", "error_message",
        ])
    except (fitz.FileDataError, fitz.EmptyFileError, LessonPipelineError) as exc:
        _mark_failed(material, exc)
        if propagate_validation_error:
            raise LessonPipelineError(str(exc)) from exc
    except Exception as exc:
        _mark_failed(material, exc)
        if propagate_validation_error:
            raise LessonPipelineUnavailableError(str(exc)) from exc
    return material
