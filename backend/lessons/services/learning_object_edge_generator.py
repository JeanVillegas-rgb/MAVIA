from __future__ import annotations

import math
import re
from functools import lru_cache

from django.core.exceptions import ValidationError
from django.db import transaction

from lessons.models import CourseGroup, LearningObject, LearningObjectPrerequisiteEdge, OutlineNode


EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
MIN_SEMANTIC_SIMILARITY = 0.42
MIN_DIRECTION_SCORE = 0.30
MIN_FINAL_SCORE = 0.52
MAX_OUTGOING_EDGES = 3
MAX_INCOMING_EDGES = 4

GENERIC_WORDS = {
    "lesson",
    "learning",
    "object",
    "objects",
    "concept",
    "topic",
    "module",
    "activity",
    "students",
    "learners",
    "teacher",
    "material",
    "materials",
    "matter",
}

DEPENDENCY_PATTERNS = [
    "depends on",
    "builds on",
    "requires",
    "before",
    "first",
    "prerequisite",
    "using",
    "apply",
    "classify",
    "compare",
    "group",
    "identify",
    "understand",
]

APPLICATION_TERMS = {"classify", "classification", "group", "grouping", "compare", "apply", "everyday"}
FOUNDATION_TERMS = {"define", "definition", "basic", "property", "properties", "state", "states", "solid", "liquid", "gas"}


def _clamp(value):
    return max(0.0, min(1.0, float(value)))


@lru_cache(maxsize=1)
def _get_embedding_model():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError("sentence-transformers is required for learning object DAG generation.") from exc
    return SentenceTransformer(EMBEDDING_MODEL_NAME)


def _cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return _clamp((dot / (norm_a * norm_b) + 1.0) / 2.0)


def compute_learning_object_semantic_similarity(source_text, target_text):
    model = _get_embedding_model()
    source_embedding, target_embedding = model.encode([source_text, target_text])
    return _cosine_similarity(source_embedding, target_embedding)


def _tokens(value):
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (value or "").lower())
        if len(token) > 2 and token not in GENERIC_WORDS
    }


def _object_text(obj: LearningObject):
    return re.sub(r"\s+", " ", f"{obj.title}. {obj.content}".strip())[:1600]


def _title_overlap(source: LearningObject, target: LearningObject):
    source_tokens = _tokens(f"{source.title} {source.content[:400]}")
    target_tokens = _tokens(f"{target.title} {target.content[:400]}")
    if not source_tokens or not target_tokens:
        return 0.0
    return _clamp(len(source_tokens & target_tokens) / max(min(len(source_tokens), len(target_tokens)), 1))


def _source_order(source: LearningObject, target: LearningObject):
    source_key = (source.material_id, source.order, source.id)
    target_key = (target.material_id, target.order, target.id)
    if source_key < target_key:
        return 1.0
    return 0.0


def _dependency_cue(source: LearningObject, target: LearningObject):
    target_text = _object_text(target).lower()
    source_tokens = _tokens(source.title)
    cue = any(pattern in target_text for pattern in DEPENDENCY_PATTERNS)
    mentioned = source.title.lower() in target_text or bool(source_tokens & _tokens(target_text))
    if cue and mentioned:
        return 1.0
    if cue:
        return 0.55
    return 0.0


def _pedagogical_direction(source: LearningObject, target: LearningObject):
    source_tokens = _tokens(_object_text(source))
    target_tokens = _tokens(_object_text(target))
    if not source_tokens or not target_tokens:
        return 0.0
    score = 0.0
    if (source_tokens & FOUNDATION_TERMS) and (target_tokens & APPLICATION_TERMS) and (source_tokens & target_tokens):
        score = max(score, 0.72)
    if source_tokens and source_tokens.issubset(target_tokens):
        score = max(score, 0.60)
    if source.title.lower() in target.content.lower():
        score = max(score, 0.58)
    return _clamp(score)


def _score_candidate(source: LearningObject, target: LearningObject):
    semantic = compute_learning_object_semantic_similarity(_object_text(source), _object_text(target))
    overlap = _title_overlap(source, target)
    dependency = _dependency_cue(source, target)
    pedagogical = _pedagogical_direction(source, target)
    order = _source_order(source, target)
    relatedness = _clamp(0.75 * semantic + 0.25 * overlap)
    direction = _clamp(0.35 * dependency + 0.25 * pedagogical + 0.20 * order)
    final = _clamp(0.45 * relatedness + 0.55 * direction)
    return {
        "source": source,
        "target": target,
        "score": final,
        "semantic_similarity": semantic,
        "dependency_cue_score": dependency,
        "source_order_score": order,
        "title_overlap_score": overlap,
        "pedagogical_relation_score": pedagogical,
        "direction_score": direction,
        "relatedness_score": relatedness,
    }


def learning_object_path_exists(module: OutlineNode, source_id, target_id, extra_edges=None, ignore_edge_id=None):
    adjacency = {}
    edges = LearningObjectPrerequisiteEdge.objects.filter(module_node=module).exclude(
        validation_status=LearningObjectPrerequisiteEdge.ValidationStatus.REJECTED
    )
    if ignore_edge_id is not None:
        edges = edges.exclude(id=ignore_edge_id)
    for source, target in edges.values_list("source_id", "target_id"):
        adjacency.setdefault(source, set()).add(target)
    for source, target in extra_edges or []:
        adjacency.setdefault(source, set()).add(target)
    stack = list(adjacency.get(source_id, set()))
    seen = set()
    while stack:
        current = stack.pop()
        if current == target_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(adjacency.get(current, set()) - seen)
    return False


def learning_object_edge_would_create_cycle(module, source_id, target_id, extra_edges=None, ignore_edge_id=None):
    return source_id == target_id or learning_object_path_exists(
        module,
        target_id,
        source_id,
        extra_edges=extra_edges,
        ignore_edge_id=ignore_edge_id,
    )


def _would_cycle(module, source_id, target_id, extra_edges=None):
    return learning_object_edge_would_create_cycle(module, source_id, target_id, extra_edges=extra_edges)


def is_learning_object_dag_acyclic(module):
    for source, target, edge_id in LearningObjectPrerequisiteEdge.objects.filter(module_node=module).exclude(
        validation_status=LearningObjectPrerequisiteEdge.ValidationStatus.REJECTED
    ).values_list("source_id", "target_id", "id"):
        if learning_object_edge_would_create_cycle(module, source, target, ignore_edge_id=edge_id):
            return False
    return True


def _explanation(candidate):
    return (
        f"'{candidate['source'].title}' is proposed before '{candidate['target'].title}'. "
        f"Scores: final {candidate['score']:.2f}, semantic {candidate['semantic_similarity']:.2f}, "
        f"dependency {candidate['dependency_cue_score']:.2f}, source order {candidate['source_order_score']:.2f}, "
        f"title overlap {candidate['title_overlap_score']:.2f}."
    )


def generate_module_learning_object_dag(course: CourseGroup, module: OutlineNode):
    if module.course_id != course.id or module.parent_id is not None:
        raise ValueError("Learning object DAG generation requires a top-level module in this course.")

    objects = list(
        LearningObject.objects.filter(material__course=course, material__module_node=module)
        .select_related("material")
        .order_by("material_id", "order", "id")
    )
    summary = {
        "module_id": module.id,
        "learning_objects_considered": len(objects),
        "candidate_pairs_evaluated": 0,
        "within_pdf_pairs_evaluated": 0,
        "cross_pdf_pairs_evaluated": 0,
        "edges_created": 0,
        "edges_updated": 0,
        "edges_deleted": 0,
        "edges_rejected_for_cycle": 0,
        "edges_skipped_below_threshold": 0,
        "edges_skipped_for_insufficient_direction": 0,
        "is_acyclic": True,
    }
    if len(objects) < 2:
        return summary

    candidates = []
    for source in objects:
        for target in objects:
            if source.id == target.id:
                continue
            if _source_order(source, target) <= 0:
                continue
            summary["candidate_pairs_evaluated"] += 1
            if source.material_id == target.material_id:
                summary["within_pdf_pairs_evaluated"] += 1
            else:
                summary["cross_pdf_pairs_evaluated"] += 1
            candidate = _score_candidate(source, target)
            if candidate["semantic_similarity"] < MIN_SEMANTIC_SIMILARITY or candidate["score"] < MIN_FINAL_SCORE:
                summary["edges_skipped_below_threshold"] += 1
                continue
            if candidate["direction_score"] < MIN_DIRECTION_SCORE or (
                candidate["dependency_cue_score"] <= 0 and candidate["pedagogical_relation_score"] <= 0
            ):
                summary["edges_skipped_for_insufficient_direction"] += 1
                continue
            candidates.append(candidate)

    outgoing = {}
    incoming = {}
    accepted = []
    generated_keys = set()
    with transaction.atomic():
        for candidate in sorted(candidates, key=lambda item: (-item["score"], item["source"].order, item["target"].order)):
            source = candidate["source"]
            target = candidate["target"]
            if outgoing.get(source.id, 0) >= MAX_OUTGOING_EDGES or incoming.get(target.id, 0) >= MAX_INCOMING_EDGES:
                continue
            if _would_cycle(module, source.id, target.id, extra_edges=accepted):
                summary["edges_rejected_for_cycle"] += 1
                continue
            generated_keys.add((source.id, target.id))
            accepted.append((source.id, target.id))
            edge = LearningObjectPrerequisiteEdge.objects.filter(
                course=course,
                module_node=module,
                source=source,
                target=target,
            ).first()
            defaults = {
                "score": candidate["score"],
                "semantic_similarity": candidate["semantic_similarity"],
                "dependency_cue_score": candidate["dependency_cue_score"],
                "source_order_score": candidate["source_order_score"],
                "title_overlap_score": candidate["title_overlap_score"],
                "explanation": _explanation(candidate),
                "validation_status": LearningObjectPrerequisiteEdge.ValidationStatus.PENDING,
                "is_manual": False,
            }
            if edge:
                if edge.is_manual or edge.validation_status == LearningObjectPrerequisiteEdge.ValidationStatus.APPROVED:
                    continue
                for field, value in defaults.items():
                    setattr(edge, field, value)
                edge.full_clean()
                edge.save()
                summary["edges_updated"] += 1
            else:
                edge = LearningObjectPrerequisiteEdge(course=course, module_node=module, source=source, target=target, **defaults)
                edge.full_clean()
                edge.save()
                summary["edges_created"] += 1
            outgoing[source.id] = outgoing.get(source.id, 0) + 1
            incoming[target.id] = incoming.get(target.id, 0) + 1

        stale_ids = [
            edge.id
            for edge in LearningObjectPrerequisiteEdge.objects.filter(
                course=course,
                module_node=module,
                validation_status=LearningObjectPrerequisiteEdge.ValidationStatus.PENDING,
                is_manual=False,
            )
            if (edge.source_id, edge.target_id) not in generated_keys
        ]
        if stale_ids:
            summary["edges_deleted"] = LearningObjectPrerequisiteEdge.objects.filter(id__in=stale_ids).delete()[0]

    summary["is_acyclic"] = True
    return summary
