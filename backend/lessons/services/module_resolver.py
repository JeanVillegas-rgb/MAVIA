from __future__ import annotations

from lessons.models import ExtractedConcept, LearningMaterial, OutlineNode


def get_top_level_module(outline_node: OutlineNode | None) -> OutlineNode | None:
    if outline_node is None:
        return None

    current = outline_node
    while current.parent_id is not None:
        current = current.parent
    return current


def get_material_module(material: LearningMaterial) -> OutlineNode | None:
    if material.module_node_id:
        return material.module_node
    return get_top_level_module(material.outline_node)


def get_concept_module(concept: ExtractedConcept) -> OutlineNode:
    return concept.module_node
