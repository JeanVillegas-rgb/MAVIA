from __future__ import annotations

import math
from functools import lru_cache

from django.db import transaction

from lessons.models import CourseGroup, OutlineEdge, OutlineNode


# Initial candidate threshold. This should be evaluated and tuned later using
# teacher-reviewed prerequisite pairs from real courses.
EDGE_SCORE_THRESHOLD = 0.70
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
MAX_ANCESTOR_DEPTH_GAP = 2
MAX_FORWARD_DISTANCE = 3
MAX_OUTGOING_EDGES_PER_NODE = 2
MAX_INCOMING_EDGES_PER_NODE = 3


def _clamp_score(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@lru_cache(maxsize=1)
def _get_embedding_model():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required for prerequisite edge generation. "
            "Install backend requirements before running this service."
        ) from exc

    return SentenceTransformer(EMBEDDING_MODEL_NAME)


def _cosine_similarity(source_embedding, target_embedding) -> float:
    dot_product = 0.0
    source_norm = 0.0
    target_norm = 0.0

    for source_value, target_value in zip(source_embedding, target_embedding):
        dot_product += source_value * target_value
        source_norm += source_value * source_value
        target_norm += target_value * target_value

    if source_norm <= 0.0 or target_norm <= 0.0:
        return 0.0

    cosine = dot_product / (math.sqrt(source_norm) * math.sqrt(target_norm))
    return _clamp_score((cosine + 1.0) / 2.0)


def compute_semantic_similarity(source: OutlineNode, target: OutlineNode) -> float:
    model = _get_embedding_model()
    source_embedding, target_embedding = model.encode([source.title, target.title])
    return _cosine_similarity(source_embedding, target_embedding)


def compute_outline_order_score(source: OutlineNode, target: OutlineNode) -> float:
    return 1.0 if source.order < target.order else 0.0


def _is_ancestor(source: OutlineNode, target: OutlineNode) -> bool:
    current = target.parent
    while current is not None:
        if current.id == source.id:
            return True
        current = current.parent
    return False


def _ancestor_depth_gap(source: OutlineNode, target: OutlineNode) -> int | None:
    current = target.parent
    gap = 1
    while current is not None:
        if current.id == source.id:
            return gap
        current = current.parent
        gap += 1
    return None


def _top_level_section_id(node: OutlineNode) -> int:
    current = node
    while current.parent is not None:
        current = current.parent
    return current.id


def _is_module_node(node: OutlineNode) -> bool:
    return node.parent_id is None


def _is_concept_node(node: OutlineNode) -> bool:
    return node.parent_id is not None


def _same_module(source: OutlineNode, target: OutlineNode) -> bool:
    return _top_level_section_id(source) == _top_level_section_id(target)


def is_direct_sibling_pair(source: OutlineNode, target: OutlineNode) -> bool:
    return (
        source.parent_id is not None
        and source.parent_id == target.parent_id
        and source.depth == target.depth
    )


def is_valid_candidate_pair(
    source: OutlineNode,
    target: OutlineNode,
    forward_distance: int = 1,
    semantic_similarity: float | None = None,
) -> bool:
    if source.id == target.id:
        return False

    if source.course_id != target.course_id:
        return False

    if _is_module_node(source) or _is_module_node(target):
        return False

    if not _same_module(source, target):
        return False

    ancestor_gap = _ancestor_depth_gap(source, target)
    if ancestor_gap is not None:
        return ancestor_gap <= MAX_ANCESTOR_DEPTH_GAP

    if forward_distance > MAX_FORWARD_DISTANCE:
        return False

    return True


def compute_edge_score(semantic_similarity: float, outline_order_score: float) -> float:
    return _clamp_score((semantic_similarity + outline_order_score) / 2.0)


def _build_explanation(source: OutlineNode, target: OutlineNode, semantic_similarity: float) -> str:
    return (
        f"{source.title} appears before {target.title} in the outline and has "
        f"semantic similarity {semantic_similarity:.2f}."
    )


def _save_candidate_edge(
    course: CourseGroup,
    source: OutlineNode,
    target: OutlineNode,
    score: float,
    semantic_similarity: float,
    outline_order_score: float,
) -> str | None:
    edge = OutlineEdge.objects.filter(course=course, source=source, target=target).first()
    explanation = _build_explanation(source, target, semantic_similarity)

    if edge:
        if edge.validation_status != OutlineEdge.ValidationStatus.PENDING or edge.is_manual:
            return None

        edge.score = score
        edge.semantic_similarity = semantic_similarity
        edge.outline_order_score = outline_order_score
        edge.explanation = explanation
        edge.validation_status = OutlineEdge.ValidationStatus.PENDING
        edge.is_manual = False
        edge.full_clean()
        edge.save(
            update_fields=[
                "score",
                "semantic_similarity",
                "outline_order_score",
                "explanation",
                "validation_status",
                "is_manual",
                "updated_at",
            ]
        )
        return "updated"

    edge = OutlineEdge(
        course=course,
        source=source,
        target=target,
        score=score,
        semantic_similarity=semantic_similarity,
        outline_order_score=outline_order_score,
        explanation=explanation,
        validation_status=OutlineEdge.ValidationStatus.PENDING,
        is_manual=False,
    )
    edge.full_clean()
    edge.save()
    return "created"


def _candidate_sort_key(candidate: dict) -> tuple:
    return (
        -candidate["score"],
        -candidate["semantic_similarity"],
        candidate["forward_distance"],
        candidate["source"].order,
        candidate["target"].order,
        candidate["target"].id,
    )


def _apply_outgoing_limit(candidates: list[dict]) -> tuple[list[dict], int]:
    selected: list[dict] = []
    removed = 0
    by_source: dict[int, list[dict]] = {}

    for candidate in candidates:
        by_source.setdefault(candidate["source"].id, []).append(candidate)

    for source_candidates in by_source.values():
        ordered = sorted(source_candidates, key=_candidate_sort_key)
        selected.extend(ordered[:MAX_OUTGOING_EDGES_PER_NODE])
        removed += max(0, len(ordered) - MAX_OUTGOING_EDGES_PER_NODE)

    return selected, removed


def _apply_incoming_limit(candidates: list[dict]) -> tuple[list[dict], int]:
    selected_keys: set[tuple[int, int]] = set()
    removed = 0
    by_target: dict[int, list[dict]] = {}

    for candidate in candidates:
        by_target.setdefault(candidate["target"].id, []).append(candidate)

    for target_candidates in by_target.values():
        ordered = sorted(target_candidates, key=_candidate_sort_key)
        kept = ordered[:MAX_INCOMING_EDGES_PER_NODE]
        selected_keys.update((candidate["source"].id, candidate["target"].id) for candidate in kept)
        removed += max(0, len(ordered) - MAX_INCOMING_EDGES_PER_NODE)

    selected = [
        candidate
        for candidate in candidates
        if (candidate["source"].id, candidate["target"].id) in selected_keys
    ]
    return selected, removed


def _descendant_ids(module_node: OutlineNode) -> set[int]:
    descendants: set[int] = set()
    stack = list(module_node.children.all())
    while stack:
        node = stack.pop()
        descendants.add(node.id)
        stack.extend(node.children.all())
    return descendants


def generate_prerequisite_edges(course: CourseGroup, module_node: OutlineNode | None = None) -> dict:
    nodes = list(
        OutlineNode.objects.filter(course=course)
        .select_related("parent", "course")
        .prefetch_related("children")
        .order_by("depth", "order", "id")
    )

    if module_node is not None:
        if module_node.course_id != course.id:
            raise ValueError("Module node must belong to the selected course.")
        if module_node.parent_id is not None:
            raise ValueError("Module node must be a top-level outline node.")
        allowed_ids = _descendant_ids(module_node)
        nodes = [node for node in nodes if node.id in allowed_ids]

    concept_nodes = [node for node in nodes if _is_concept_node(node)]
    concepts_by_module: dict[int, list[OutlineNode]] = {}
    for node in concept_nodes:
        concepts_by_module.setdefault(_top_level_section_id(node), []).append(node)
    for module_concepts in concepts_by_module.values():
        module_concepts.sort(key=lambda node: (node.order, node.depth, node.id))

    total_concept_pairs = max(0, (len(concept_nodes) * (len(concept_nodes) - 1)) // 2)
    same_module_pairs = sum(
        max(0, (len(module_concepts) * (len(module_concepts) - 1)) // 2)
        for module_concepts in concepts_by_module.values()
    )
    generated_edge_keys: set[tuple[int, int]] = set()
    summary = {
        "total_possible_forward_pairs": same_module_pairs,
        "pairs_considered_after_filtering": 0,
        "pairs_skipped_by_distance": 0,
        "pairs_skipped_as_siblings": 0,
        "pairs_skipped_cross_section": total_concept_pairs - same_module_pairs,
        "pairs_skipped_cross_module": total_concept_pairs - same_module_pairs,
        "candidates_above_threshold": 0,
        "candidates_removed_by_outgoing_limit": 0,
        "candidates_removed_by_incoming_limit": 0,
        "edges_created": 0,
        "edges_updated": 0,
        "edges_deleted": 0,
        "threshold": EDGE_SCORE_THRESHOLD,
        "max_forward_distance": MAX_FORWARD_DISTANCE,
        "max_outgoing_edges_per_node": MAX_OUTGOING_EDGES_PER_NODE,
        "max_incoming_edges_per_node": MAX_INCOMING_EDGES_PER_NODE,
        "module_count": len([node for node in nodes if _is_module_node(node)]) if module_node is None else 1,
        "module_id": module_node.id if module_node is not None else None,
        "module_title": module_node.title if module_node is not None else "",
        "concept_count": len(concept_nodes),
    }
    candidates_above_threshold: list[dict] = []

    with transaction.atomic():
        for module_concepts in concepts_by_module.values():
            for source_index, source in enumerate(module_concepts):
                for target_index, target in enumerate(module_concepts[source_index + 1 :], start=source_index + 1):
                    forward_distance = target_index - source_index

                    ancestor_gap = _ancestor_depth_gap(source, target)
                    if ancestor_gap is not None and ancestor_gap > MAX_ANCESTOR_DEPTH_GAP:
                        summary["pairs_skipped_by_distance"] += 1
                        continue

                    if ancestor_gap is None and forward_distance > MAX_FORWARD_DISTANCE:
                        summary["pairs_skipped_by_distance"] += 1
                        continue

                    semantic_similarity = _clamp_score(compute_semantic_similarity(source, target))

                    if not is_valid_candidate_pair(source, target, forward_distance, semantic_similarity):
                        continue

                    summary["pairs_considered_after_filtering"] += 1
                    outline_order_score = _clamp_score(compute_outline_order_score(source, target))
                    score = compute_edge_score(semantic_similarity, outline_order_score)

                    if score < EDGE_SCORE_THRESHOLD:
                        continue

                    summary["candidates_above_threshold"] += 1
                    candidates_above_threshold.append(
                        {
                            "source": source,
                            "target": target,
                            "score": score,
                            "semantic_similarity": semantic_similarity,
                            "outline_order_score": outline_order_score,
                            "forward_distance": forward_distance,
                        }
                    )

        outgoing_limited_candidates, removed_by_outgoing = _apply_outgoing_limit(candidates_above_threshold)
        final_candidates, removed_by_incoming = _apply_incoming_limit(outgoing_limited_candidates)
        summary["candidates_removed_by_outgoing_limit"] = removed_by_outgoing
        summary["candidates_removed_by_incoming_limit"] = removed_by_incoming

        for candidate in final_candidates:
            generated_edge_keys.add((candidate["source"].id, candidate["target"].id))
            result = _save_candidate_edge(
                course,
                candidate["source"],
                candidate["target"],
                candidate["score"],
                candidate["semantic_similarity"],
                candidate["outline_order_score"],
            )
            if result == "created":
                summary["edges_created"] += 1
            elif result == "updated":
                summary["edges_updated"] += 1

        stale_edges = OutlineEdge.objects.filter(
            course=course,
            validation_status=OutlineEdge.ValidationStatus.PENDING,
            is_manual=False,
        )
        if module_node is not None:
            module_concept_ids = set(concept_node.id for concept_node in concept_nodes)
            stale_edges = stale_edges.filter(source_id__in=module_concept_ids, target_id__in=module_concept_ids)
        stale_edge_ids = [
            edge.id
            for edge in stale_edges
            if (edge.source_id, edge.target_id) not in generated_edge_keys
        ]
        if stale_edge_ids:
            summary["edges_deleted"] = OutlineEdge.objects.filter(id__in=stale_edge_ids).delete()[0]

    summary["pairs_evaluated"] = summary["pairs_considered_after_filtering"]
    summary["edges_skipped_as_siblings"] = summary["pairs_skipped_as_siblings"]
    return summary
