from __future__ import annotations

import textwrap

from django.db import transaction

from lessons.models import ConceptSource, ExtractedConcept, LearningMaterial, OutlineNode, normalize_concept_title

from .content_generator import extract_pdf_text
from .llm_client import extract_json_from_text, get_llm_client


class ConceptExtractionError(Exception):
    pass


def _limited_text(text: str, limit: int = 28000) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit]


def _coerce_confidence(value):
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence < 0.0:
        return 0.0
    if confidence > 1.0:
        return 1.0
    return confidence


def _coerce_positive_integer(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _build_prompt(module_node: OutlineNode, material_payloads: list[dict]) -> str:
    materials_text = "\n\n".join(
        textwrap.dedent(
            f"""
            MATERIAL_ID: {payload["id"]}
            MATERIAL_TITLE: {payload["title"]}
            LESSON_CONTENT_TEXT:
            {payload["text"]}
            """
        ).strip()
        for payload in material_payloads
    )

    return textwrap.dedent(
        f"""
        You are extracting teachable concept candidates from teacher-uploaded PDFs for one course module.

        MODULE_TITLE:
        {module_node.title}

        TASK:
        Return distinct teachable concepts that can become DAG nodes, mastery targets, prerequisite nodes,
        remediation targets, or assessment tags.

        OUTPUT JSON ONLY:
        {{
          "concepts": [
            {{
              "title": "Canonical concept title",
              "description": "Short instructional description",
              "confidence": 0.91,
              "section_title": "Optional source section",
              "page_number": 2,
              "source_excerpt": "Short supporting excerpt from the PDF",
              "first_appearance_order": 1,
              "material_id": 123
            }}
          ]
        }}

        RULES:
        - Use only the lesson_content text shown below.
        - Do not assume a fixed number of concepts.
        - Do not extract every noun, sentence, paragraph, caption, isolated fact, instruction, or example.
        - Do not include module names, PDF filenames, teacher instructions, or generic words as concepts.
        - Merge obvious duplicate concepts in this response.
        - Include material_id from the source material when possible.

        MODULE PDFS:
        {materials_text}
        """
    ).strip()


def _call_concept_llm(module_node: OutlineNode, material_payloads: list[dict]) -> list[dict]:
    response = get_llm_client().generate_text(_build_prompt(module_node, material_payloads), max_tokens=2500, timeout=300)
    output = response.get("text") if isinstance(response, dict) else None
    data = extract_json_from_text(output) if output else response
    concepts = data.get("concepts") if isinstance(data, dict) else None
    if not isinstance(concepts, list):
        raise ConceptExtractionError("The LLM did not return a valid concept list.")
    return [concept for concept in concepts if isinstance(concept, dict)]


def _get_source_material(item: dict, materials: list[LearningMaterial]) -> LearningMaterial:
    material_by_id = {material.id: material for material in materials}
    try:
        material_id = int(item.get("material_id"))
    except (TypeError, ValueError):
        material_id = None
    return material_by_id.get(material_id) or materials[0]


def _save_concept_source(concept: ExtractedConcept, material: LearningMaterial, item: dict):
    source_excerpt = item.get("source_excerpt") or ""
    if source_excerpt and source_excerpt not in (material.extracted_text or ""):
        source_excerpt = _find_source_excerpt(material.extracted_text or "", source_excerpt)
    source_defaults = {
        "section_title": (item.get("section_title") or "")[:255],
        "source_excerpt": source_excerpt,
        "first_appearance_order": _coerce_positive_integer(item.get("first_appearance_order")) or concept.order,
    }
    page_number = _coerce_positive_integer(item.get("page_number"))

    existing_source = ConceptSource.objects.filter(
        concept=concept,
        learning_material=material,
        page_number=page_number,
        section_title=source_defaults["section_title"],
        source_excerpt=source_defaults["source_excerpt"],
    ).first()
    if existing_source:
        return existing_source, False

    return ConceptSource.objects.create(
        concept=concept,
        learning_material=material,
        page_number=page_number,
        **source_defaults,
    ), True


def _find_source_excerpt(text: str, requested_excerpt: str) -> str:
    if not text.strip():
        return ""
    requested_words = [
        word
        for word in requested_excerpt.split()
        if len(word) > 3
    ][:4]
    if not requested_words:
        return ""
    paragraphs = [paragraph.strip() for paragraph in text.splitlines() if paragraph.strip()]
    for paragraph in paragraphs:
        lowered = paragraph.lower()
        if all(word.lower().strip(".,:;!?") in lowered for word in requested_words):
            return paragraph
    return ""


def _lesson_content_text_from_generated_json(material: LearningMaterial) -> str:
    generated_json = material.generated_json or {}
    classified_blocks = generated_json.get("classified_blocks")
    if isinstance(classified_blocks, list):
        lesson_texts = [
            block.get("text", "")
            for block in classified_blocks
            if isinstance(block, dict) and block.get("category") == "lesson_content"
        ]
        lesson_text = "\n".join(text for text in lesson_texts if text.strip()).strip()
        if lesson_text:
            return lesson_text

    narration = generated_json.get("narration_script")
    if isinstance(narration, list):
        lesson_texts = [
            item.get("content", "")
            for item in narration
            if isinstance(item, dict) and item.get("type") in {"lesson_content", "teacher_text"}
        ]
        lesson_text = "\n".join(text for text in lesson_texts if text.strip()).strip()
        if lesson_text:
            return lesson_text

    return material.extracted_text or ""


@transaction.atomic
def extract_module_concepts(module_node: OutlineNode) -> dict:
    if module_node.parent_id is not None:
        raise ConceptExtractionError("Concept extraction requires a top-level module.")

    materials = list(
        LearningMaterial.objects.filter(course=module_node.course, module_node=module_node)
        .order_by("created_at", "id")
    )
    if not materials:
        raise ConceptExtractionError("This module does not have uploaded PDF materials.")

    material_payloads = []
    for material in materials:
        text = _lesson_content_text_from_generated_json(material)
        if not text.strip():
            text = extract_pdf_text(material.pdf_file.path)
            material.extracted_text = text
            material.save(update_fields=["extracted_text"])
        if text.strip():
            material_payloads.append(
                {
                    "id": material.id,
                    "title": material.title,
                    "text": _limited_text(text),
                }
            )

    if not material_payloads:
        raise ConceptExtractionError("No readable text was found in this module's PDF materials.")

    extracted_items = _call_concept_llm(module_node, material_payloads)

    concepts_created = 0
    concepts_updated = 0
    duplicates_merged = 0
    source_records_created = 0

    next_order = (
        ExtractedConcept.objects.filter(course=module_node.course, module_node=module_node).count()
    )

    for item in extracted_items:
        title = (item.get("title") or "").strip()
        normalized_title = normalize_concept_title(title)
        if not normalized_title:
            continue

        material = _get_source_material(item, materials)
        description = (item.get("description") or "").strip()
        confidence = _coerce_confidence(item.get("confidence"))

        concept = ExtractedConcept.objects.filter(
            course=module_node.course,
            module_node=module_node,
            normalized_title=normalized_title,
        ).first()

        if concept is None:
            concept = ExtractedConcept.objects.create(
                course=module_node.course,
                module_node=module_node,
                canonical_title=title[:255],
                description=description,
                order=_coerce_positive_integer(item.get("first_appearance_order")) or next_order,
                confidence=confidence,
            )
            next_order += 1
            concepts_created += 1
        else:
            can_update_generated_fields = (
                concept.validation_status == ExtractedConcept.ValidationStatus.PENDING
                and not concept.is_manual
            )
            if can_update_generated_fields:
                changed_fields = []
                if description and concept.description != description:
                    concept.description = description
                    changed_fields.append("description")
                if confidence is not None and concept.confidence != confidence:
                    concept.confidence = confidence
                    changed_fields.append("confidence")
                if changed_fields:
                    concept.save(update_fields=[*changed_fields, "updated_at"])
                    concepts_updated += 1
            duplicates_merged += 1

        _, source_created = _save_concept_source(concept, material, item)
        if source_created:
            source_records_created += 1

    concepts = (
        ExtractedConcept.objects.filter(course=module_node.course, module_node=module_node)
        .prefetch_related("sources__learning_material")
        .order_by("order", "id")
    )

    return {
        "module": module_node,
        "materials_processed": len(material_payloads),
        "concepts_created": concepts_created,
        "concepts_updated": concepts_updated,
        "duplicates_merged": duplicates_merged,
        "source_records_created": source_records_created,
        "concepts": concepts,
    }
