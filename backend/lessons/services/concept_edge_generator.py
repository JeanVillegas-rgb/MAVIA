from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from functools import lru_cache

from django.core.exceptions import ValidationError
from django.db import transaction

from lessons.models import ConceptPrerequisiteEdge, CourseGroup, ExtractedConcept, OutlineNode

from .concept_dag_state import invalidate_module_dag


EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
SCORE_CONFIG = {
    "minimum_semantic_similarity": 0.45,
    "minimum_direction_score": 0.35,
    "minimum_final_score": 0.55,
    "relatedness_semantic_weight": 0.75,
    "relatedness_keyterm_weight": 0.25,
    "direction_dependency_weight": 0.35,
    "direction_pedagogical_weight": 0.25,
    "direction_source_order_weight": 0.20,
    "direction_llm_weight": 0.20,
    "final_relatedness_weight": 0.45,
    "final_direction_weight": 0.55,
    "max_auto_outgoing_edges": 3,
    "max_auto_incoming_edges": 4,
    "concept_text_source_limit": 900,
    "top_rejected_limit": 10,
}
CONCEPT_EDGE_SCORE_THRESHOLD = SCORE_CONFIG["minimum_final_score"]
MAX_AUTO_OUTGOING_EDGES = SCORE_CONFIG["max_auto_outgoing_edges"]
MAX_AUTO_INCOMING_EDGES = SCORE_CONFIG["max_auto_incoming_edges"]

DEPENDENCY_CUE_PATTERNS = [
    "requires",
    "requires knowledge of",
    "based on",
    "builds on",
    "depends on",
    "before learning",
    "prerequisite",
    "using the concept of",
    "using",
    "apply",
    "applies",
    "to understand this",
    "to understand",
    "first understand",
    "previously learned",
    "knowledge of",
    "needed to understand",
    "foundation for",
    "classify according to",
    "identify using",
    "distinguish through",
]

GENERIC_CONCEPT_WORDS = {
    "matter",
    "material",
    "materials",
    "lesson",
    "introduction",
    "science",
    "concept",
    "concepts",
    "topic",
    "module",
    "learning",
    "learner",
    "learners",
    "students",
    "student",
    "teacher",
    "activity",
    "activities",
    "pdf",
    "content",
    "based",
    "using",
}

CLASSIFICATION_APPLICATION_TERMS = {
    "classify",
    "classification",
    "group",
    "grouping",
    "compare",
    "comparison",
    "application",
    "apply",
    "identify",
    "distinguish",
    "explain",
    "analyze",
    "analysis",
    "effects",
    "effect",
    "useful",
    "harmful",
}

FOUNDATION_TERMS = {
    "define",
    "definition",
    "basic",
    "foundation",
    "property",
    "properties",
    "characteristic",
    "characteristics",
    "observable",
    "state",
    "states",
    "part",
    "parts",
    "component",
    "components",
    "cause",
    "mechanism",
    "process",
}

CAUSE_EFFECT_TERMS = {
    "cause",
    "causes",
    "mechanism",
    "oxygen",
    "heat",
    "result",
    "results",
    "effect",
    "effects",
    "change",
    "changes",
}


@dataclass(frozen=True)
class ConceptEvidence:
    concept: ExtractedConcept
    text: str
    first_material_order: int
    first_page_number: int
    first_appearance_order: int
    source_count: int


def _clamp_score(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@lru_cache(maxsize=1)
def _get_embedding_model():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required for concept prerequisite generation. "
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


def _tokenize(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (value or "").lower())
        if len(token) > 2 and token not in GENERIC_CONCEPT_WORDS
    }


def _clean_evidence_text(value: str, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = []
    seen = set()
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        key = sentence.casefold()
        if key in seen:
            continue
        seen.add(key)
        kept.append(sentence)
        if limit and len(" ".join(kept)) >= limit:
            break
    cleaned = " ".join(kept)
    return cleaned[:limit].strip() if limit else cleaned


def _concept_text(concept: ExtractedConcept) -> str:
    source_limit = int(SCORE_CONFIG["concept_text_source_limit"])
    source_parts = []
    material_titles = []
    for source in concept.sources.all():
        if source.learning_material and source.learning_material.title:
            material_titles.append(source.learning_material.title)
        if source.section_title:
            source_parts.append(source.section_title)
        if source.source_excerpt:
            source_parts.append(source.source_excerpt)
    source_text = _clean_evidence_text(" ".join(source_parts), limit=source_limit)
    material_text = _clean_evidence_text(" ".join(material_titles), limit=240)
    return _clean_evidence_text(
        " ".join(
            part.strip()
            for part in [concept.canonical_title, concept.description, material_text, source_text]
            if part and part.strip()
        ),
        limit=1600,
    )


def build_concept_diagnostic_rows(module: OutlineNode) -> list[dict]:
    concepts = (
        ExtractedConcept.objects.filter(module_node=module)
        .prefetch_related("sources__learning_material")
        .order_by("order", "id")
    )
    evidence = _build_evidence(list(concepts))
    rows = []
    for concept in concepts:
        sources = list(concept.sources.all())
        rows.append(
            {
                "concept_id": concept.id,
                "title": concept.canonical_title,
                "validation_status": concept.validation_status,
                "module_id": concept.module_node_id,
                "material_ids": sorted({source.learning_material_id for source in sources}),
                "source_pages": [source.page_number for source in sources],
                "source_orders": [source.first_appearance_order for source in sources],
                "description_length": len(concept.description or ""),
                "source_count": len(sources),
                "combined_evidence_text_length": len(evidence[concept.id].text if concept.id in evidence else ""),
            }
        )
    return rows


def _concept_has_evidence(concept: ExtractedConcept) -> bool:
    return bool(
        concept.description.strip()
        or any(source.source_excerpt.strip() or source.section_title.strip() for source in concept.sources.all())
    )


def _dependency_cue_score(source: ExtractedConcept, target_evidence: ConceptEvidence) -> float:
    target_text = target_evidence.text.lower()
    source_title = source.canonical_title.lower()
    source_tokens = _tokenize(source.canonical_title)
    cue_matches = [pattern for pattern in DEPENDENCY_CUE_PATTERNS if pattern in target_text]
    source_mentioned = source_title in target_text or bool(source_tokens and source_tokens & _tokenize(target_text))

    if cue_matches and source_mentioned:
        return 1.0
    if cue_matches:
        return 0.65
    if source_title in target_text:
        return 0.40
    return 0.0


def _pedagogical_relation_score(source: ExtractedConcept, target: ExtractedConcept, source_text: str, target_text: str) -> float:
    source_tokens = _tokenize(f"{source.canonical_title} {source.description} {source_text}")
    target_tokens = _tokenize(f"{target.canonical_title} {target.description} {target_text}")
    if not source_tokens or not target_tokens:
        return 0.0

    source_lower = f"{source.canonical_title} {source.description} {source_text}".lower()
    target_lower = f"{target.canonical_title} {target.description} {target_text}".lower()
    score = 0.0

    foundation_source = bool(source_tokens & FOUNDATION_TERMS)
    application_target = bool(target_tokens & CLASSIFICATION_APPLICATION_TERMS)
    if foundation_source and application_target and (source_tokens & target_tokens):
        score = max(score, 0.72)

    if source_tokens and source_tokens.issubset(target_tokens) and len(source_tokens) <= len(target_tokens):
        score = max(score, 0.68)

    target_specializes_source = bool(source_tokens & target_tokens) and len(target_tokens - source_tokens) >= 1
    if target_specializes_source:
        score = max(score, 0.42)

    source_has_components = bool(source_tokens & {"part", "parts", "component", "components", "characteristic", "characteristics"})
    target_has_system = bool(target_tokens & {"system", "process", "works", "function", "functions", "mixture", "mixtures"})
    if source_has_components and target_has_system:
        score = max(score, 0.55)

    source_has_cause = bool(source_tokens & CAUSE_EFFECT_TERMS)
    target_has_effect = bool(target_tokens & {"result", "results", "effect", "effects", "change", "changes", "undergo"})
    if source_has_cause and target_has_effect:
        score = max(score, 0.58)

    recognition_source = bool(source_tokens & {"property", "properties", "characteristic", "characteristics", "state", "states"})
    classification_target = bool(target_tokens & CLASSIFICATION_APPLICATION_TERMS)
    if recognition_source and classification_target:
        score = max(score, 0.60)

    if re.search(r"\b(classify|group|identify|distinguish)\b", target_lower) and any(token in target_lower for token in source_tokens):
        score = max(score, 0.62)
    if re.search(r"\b(define|definition|basic|properties|states)\b", source_lower) and bool(source_tokens & target_tokens):
        score = max(score, 0.45)

    return _clamp_score(score)


def _title_overlap_score(source: ExtractedConcept, target: ExtractedConcept) -> float:
    source_tokens = _tokenize(f"{source.canonical_title} {source.description}")
    target_tokens = _tokenize(f"{target.canonical_title} {target.description}")
    if not source_tokens or not target_tokens:
        return 0.0
    overlap = source_tokens & target_tokens
    return _clamp_score(len(overlap) / max(min(len(source_tokens), len(target_tokens)), 1))


def _relatedness_score(semantic_similarity: float, title_overlap_score: float) -> float:
    return _clamp_score(
        SCORE_CONFIG["relatedness_semantic_weight"] * semantic_similarity
        + SCORE_CONFIG["relatedness_keyterm_weight"] * title_overlap_score
    )


def _direction_score(
    dependency_cue_score: float,
    pedagogical_relation_score: float,
    source_order_score: float,
    llm_direction_score: float = 0.0,
) -> float:
    return _clamp_score(
        SCORE_CONFIG["direction_dependency_weight"] * dependency_cue_score
        + SCORE_CONFIG["direction_pedagogical_weight"] * pedagogical_relation_score
        + SCORE_CONFIG["direction_source_order_weight"] * source_order_score
        + SCORE_CONFIG["direction_llm_weight"] * llm_direction_score
    )


def _final_score(relatedness_score: float, direction_score: float) -> float:
    return _clamp_score(
        SCORE_CONFIG["final_relatedness_weight"] * relatedness_score
        + SCORE_CONFIG["final_direction_weight"] * direction_score
    )


def _candidate_diag(candidate: dict, accepted: bool, rejection_reason: str = "") -> dict:
    return {
        "source": {
            "id": candidate["source"].id,
            "title": candidate["source"].canonical_title,
        },
        "target": {
            "id": candidate["target"].id,
            "title": candidate["target"].canonical_title,
        },
        "score": round(candidate["score"], 4),
        "semantic_similarity": round(candidate["semantic_similarity"], 4),
        "dependency_cue_score": round(candidate["dependency_cue_score"], 4),
        "pedagogical_relation_score": round(candidate["pedagogical_relation_score"], 4),
        "source_order_score": round(candidate["source_order_score"], 4),
        "title_overlap_score": round(candidate["title_overlap_score"], 4),
        "relatedness_score": round(candidate["relatedness_score"], 4),
        "direction_score": round(candidate["direction_score"], 4),
        "accepted": accepted,
        "rejection_reason": rejection_reason,
    }


def _top_rejected(rejected: list[dict]) -> list[dict]:
    return sorted(
        rejected,
        key=lambda item: (-item["score"], -item["semantic_similarity"], item["source"]["id"], item["target"]["id"]),
    )[: int(SCORE_CONFIG["top_rejected_limit"])]


def _optional_llm_direction_scores(candidates: list[dict]) -> dict[tuple[int, int], float]:
    if os.getenv("ENABLE_CONCEPT_EDGE_LLM_REVIEW", "False").lower() not in ("1", "true", "yes"):
        return {}
    ambiguous = [
        candidate
        for candidate in candidates
        if candidate["semantic_similarity"] >= 0.55
        and candidate["direction_score"] < SCORE_CONFIG["minimum_direction_score"]
        and candidate["score"] >= SCORE_CONFIG["minimum_final_score"] - 0.12
    ][:12]
    if not ambiguous:
        return {}
    try:
        from .llm_client import extract_json_from_text, get_llm_client
    except Exception:
        return {}

    pair_lines = "\n".join(
        (
            f"- source_id={candidate['source'].id}; source_title={candidate['source'].canonical_title}; "
            f"target_id={candidate['target'].id}; target_title={candidate['target'].canonical_title}; "
            f"source_evidence={candidate['source_text'][:450]!r}; target_evidence={candidate['target_text'][:450]!r}"
        )
        for candidate in ambiguous
    )
    prompt = f"""
    Review possible prerequisite relations.

    Definition: the learner normally needs the source concept to understand, classify,
    explain, or apply the target concept.

    Rules:
    - Related does not always mean prerequisite.
    - Do not create an edge only because concepts occur in the same lesson.
    - Do not force a relationship.
    - Use only the supplied evidence.
    - Return a short factual reason, not chain-of-thought.

    Return JSON only:
    {{
      "relations": [
        {{
          "source_id": 1,
          "target_id": 2,
          "relation": "prerequisite | related_not_prerequisite | reverse | unrelated",
          "confidence": 0.0,
          "short_reason": "short reason"
        }}
      ]
    }}

    PAIRS:
    {pair_lines}
    """
    try:
        response = get_llm_client().generate_text(prompt, max_tokens=1200, timeout=240)
        output = response.get("text") if isinstance(response, dict) else None
        data = extract_json_from_text(output) if output else response
        relations = data.get("relations") if isinstance(data, dict) else []
    except Exception:
        return {}

    scores = {}
    for item in relations:
        if not isinstance(item, dict):
            continue
        try:
            source_id = int(item.get("source_id"))
            target_id = int(item.get("target_id"))
            confidence = _clamp_score(float(item.get("confidence") or 0.0))
        except (TypeError, ValueError):
            continue
        relation = item.get("relation")
        if relation == "prerequisite":
            scores[(source_id, target_id)] = confidence
        elif relation == "reverse":
            scores[(source_id, target_id)] = 0.0
    return scores


def _edge_score(
    dependency_cue_score: float,
    semantic_similarity: float,
    source_order_score: float,
    title_overlap_score: float,
) -> float:
    relatedness = _relatedness_score(semantic_similarity, title_overlap_score)
    direction = _direction_score(dependency_cue_score, 0.0, source_order_score)
    return _final_score(relatedness, direction)


def _has_directional_support(
    dependency_cue_score: float,
    semantic_similarity: float,
    source_order_score: float,
    title_overlap_score: float,
) -> bool:
    pedagogical = 0.0
    direction = _direction_score(dependency_cue_score, pedagogical, source_order_score)
    if dependency_cue_score <= 0 and pedagogical <= 0 and source_order_score > 0:
        return False
    return semantic_similarity >= SCORE_CONFIG["minimum_semantic_similarity"] and direction >= SCORE_CONFIG["minimum_direction_score"]


def _candidate_explanation(candidate: dict) -> str:
    source = candidate["source"]
    target = candidate["target"]
    reasons = []
    if candidate["dependency_cue_score"] >= 0.65:
        reasons.append("target evidence contains prerequisite/dependency language")
    if candidate["pedagogical_relation_score"] >= 0.45:
        reasons.append("pedagogical relation patterns support the direction")
    if candidate["source_order_score"] > 0:
        reasons.append("source evidence appears earlier in the module")
    if candidate["title_overlap_score"] > 0:
        reasons.append("the concepts share meaningful terminology")
    reason_text = "; ".join(reasons) if reasons else "the component signals support a prerequisite relation"
    return (
        f"'{source.canonical_title}' is proposed before '{target.canonical_title}' because {reason_text}. "
        f"Scores: final {candidate['score']:.2f}, relatedness {candidate['relatedness_score']:.2f}, "
        f"direction {candidate['direction_score']:.2f}, semantic {candidate['semantic_similarity']:.2f}, "
        f"dependency {candidate['dependency_cue_score']:.2f}, pedagogical {candidate['pedagogical_relation_score']:.2f}, "
        f"source order {candidate['source_order_score']:.2f}, title overlap {candidate['title_overlap_score']:.2f}."
    )


def _source_order_score(source: ConceptEvidence, target: ConceptEvidence) -> float:
    source_key = (
        source.first_material_order,
        source.first_page_number,
        source.first_appearance_order,
        source.concept.order,
        source.concept.id,
    )
    target_key = (
        target.first_material_order,
        target.first_page_number,
        target.first_appearance_order,
        target.concept.order,
        target.concept.id,
    )
    if source_key < target_key:
        return 1.0
    if source.concept.order < target.concept.order:
        return 0.65
    return 0.0


def _material_order(source) -> int:
    material = source.learning_material
    outline_order = material.outline_node.order if material.outline_node_id else 999999
    return outline_order * 100000 + (material.id or 0)


def _build_evidence(concepts: list[ExtractedConcept]) -> dict[int, ConceptEvidence]:
    evidence = {}
    for concept in concepts:
        sources = list(concept.sources.all())
        if sources:
            first_source = sorted(
                sources,
                key=lambda source: (
                    _material_order(source),
                    source.page_number if source.page_number is not None else 999999,
                    source.first_appearance_order,
                    source.id,
                ),
            )[0]
            first_material_order = _material_order(first_source)
            first_page_number = first_source.page_number if first_source.page_number is not None else 999999
            first_appearance_order = first_source.first_appearance_order
        else:
            first_material_order = 999999
            first_page_number = 999999
            first_appearance_order = concept.order

        evidence[concept.id] = ConceptEvidence(
            concept=concept,
            text=_concept_text(concept),
            first_material_order=first_material_order,
            first_page_number=first_page_number,
            first_appearance_order=first_appearance_order,
            source_count=len(sources),
        )
    return evidence


def compute_concept_semantic_similarity(source_text: str, target_text: str) -> float:
    model = _get_embedding_model()
    source_embedding, target_embedding = model.encode([source_text, target_text])
    return _cosine_similarity(source_embedding, target_embedding)


def _active_edges(module: OutlineNode, extra_edges: list[tuple[int, int]] | None = None):
    edges = set(
        ConceptPrerequisiteEdge.objects.filter(module_node=module)
        .exclude(validation_status=ConceptPrerequisiteEdge.ValidationStatus.REJECTED)
        .values_list("source_id", "target_id")
    )
    if extra_edges:
        edges.update(extra_edges)
    return edges


def concept_path_exists(module: OutlineNode, source_id: int, target_id: int, extra_edges=None, ignore_edge_id=None) -> bool:
    edges_queryset = ConceptPrerequisiteEdge.objects.filter(module_node=module).exclude(
        validation_status=ConceptPrerequisiteEdge.ValidationStatus.REJECTED
    )
    if ignore_edge_id is not None:
        edges_queryset = edges_queryset.exclude(id=ignore_edge_id)

    adjacency: dict[int, set[int]] = {}
    for source, target in edges_queryset.values_list("source_id", "target_id"):
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


def edge_would_create_cycle(module: OutlineNode, source_id: int, target_id: int, extra_edges=None, ignore_edge_id=None) -> bool:
    if source_id == target_id:
        return True
    return concept_path_exists(module, target_id, source_id, extra_edges=extra_edges, ignore_edge_id=ignore_edge_id)


def is_module_concept_dag_acyclic(module: OutlineNode) -> bool:
    edges = list(
        ConceptPrerequisiteEdge.objects.filter(module_node=module)
        .exclude(validation_status=ConceptPrerequisiteEdge.ValidationStatus.REJECTED)
        .values_list("source_id", "target_id")
    )
    adjacency: dict[int, set[int]] = {}
    nodes = set()
    for source, target in edges:
        adjacency.setdefault(source, set()).add(target)
        nodes.update([source, target])

    visiting = set()
    visited = set()

    def visit(node_id):
        if node_id in visiting:
            return False
        if node_id in visited:
            return True
        visiting.add(node_id)
        for target_id in adjacency.get(node_id, set()):
            if not visit(target_id):
                return False
        visiting.remove(node_id)
        visited.add(node_id)
        return True

    return all(visit(node_id) for node_id in nodes)


def validate_manual_concept_edge(course: CourseGroup, module: OutlineNode, source: ExtractedConcept, target: ExtractedConcept, edge_id=None):
    if source.id == target.id:
        raise ValidationError({"target": "Source and target cannot be the same concept."})
    if source.course_id != course.id or target.course_id != course.id:
        raise ValidationError({"detail": "Both concepts must belong to this course."})
    if source.module_node_id != module.id or target.module_node_id != module.id:
        raise ValidationError({"detail": "Both concepts must belong to this module."})
    duplicate = ConceptPrerequisiteEdge.objects.filter(
        course=course,
        module_node=module,
        source=source,
        target=target,
    )
    if edge_id is not None:
        duplicate = duplicate.exclude(id=edge_id)
    if duplicate.exists():
        raise ValidationError({"detail": "This concept prerequisite edge already exists."})
    if edge_would_create_cycle(module, source.id, target.id, ignore_edge_id=edge_id):
        raise ValidationError({"detail": "This edge would create a directed cycle."})


def _sort_key(candidate):
    return (
        -candidate["score"],
        -candidate["direction_score"],
        -candidate["relatedness_score"],
        -candidate["semantic_similarity"],
        candidate["source"].order,
        candidate["target"].order,
        candidate["target"].id,
    )


def _limit_candidates(candidates: list[dict]) -> tuple[list[dict], int, int]:
    outgoing_counts: dict[int, int] = {}
    incoming_counts: dict[int, int] = {}
    selected = []
    removed_outgoing = 0
    removed_incoming = 0
    for candidate in sorted(candidates, key=_sort_key):
        source_id = candidate["source"].id
        target_id = candidate["target"].id
        if outgoing_counts.get(source_id, 0) >= MAX_AUTO_OUTGOING_EDGES:
            removed_outgoing += 1
            continue
        if incoming_counts.get(target_id, 0) >= MAX_AUTO_INCOMING_EDGES:
            removed_incoming += 1
            continue
        selected.append(candidate)
        outgoing_counts[source_id] = outgoing_counts.get(source_id, 0) + 1
        incoming_counts[target_id] = incoming_counts.get(target_id, 0) + 1
    return selected, removed_outgoing, removed_incoming


def generate_module_concept_dag(
    course: CourseGroup,
    module: OutlineNode,
    regenerate: bool = False,
    threshold: float | None = None,
) -> dict:
    if module.course_id != course.id:
        raise ValueError("Module node must belong to the selected course.")
    if module.parent_id is not None:
        raise ValueError("Concept DAG generation requires a top-level module.")

    threshold = SCORE_CONFIG["minimum_final_score"] if threshold is None else float(threshold)
    all_module_concepts = ExtractedConcept.objects.filter(course=course, module_node=module)
    concepts = list(
        all_module_concepts.filter(
            validation_status=ExtractedConcept.ValidationStatus.APPROVED,
        )
        .prefetch_related("sources__learning_material")
        .order_by("order", "id")
    )
    summary = {
        "module_id": module.id,
        "module_title": module.title,
        "total_module_concepts": all_module_concepts.count(),
        "approved_concepts_considered": len(concepts),
        "concepts_considered": len(concepts),
        "concept_diagnostics": build_concept_diagnostic_rows(module),
        "candidate_pairs_evaluated": 0,
        "edges_created": 0,
        "edges_updated": 0,
        "edges_deleted": 0,
        "edges_rejected_for_cycle": 0,
        "edges_skipped_below_threshold": 0,
        "edges_skipped_for_insufficient_direction": 0,
        "edges_skipped_without_direction": 0,
        "edges_skipped_for_insufficient_semantic_relationship": 0,
        "edges_skipped_reverse_order_without_direction": 0,
        "edges_skipped_as_redundant": 0,
        "edges_skipped_rejected_by_teacher": 0,
        "edges_skipped_duplicate": 0,
        "candidates_removed_by_outgoing_limit": 0,
        "candidates_removed_by_incoming_limit": 0,
        "threshold": threshold,
        "score_configuration": {
            "minimum_semantic_similarity": SCORE_CONFIG["minimum_semantic_similarity"],
            "minimum_direction_score": SCORE_CONFIG["minimum_direction_score"],
            "minimum_final_score": threshold,
            "relatedness_formula": "0.75 * semantic_similarity + 0.25 * title_or_keyterm_relation",
            "direction_formula": "0.35 * explicit_dependency_cue + 0.25 * pedagogical_relation + 0.20 * source_order + 0.20 * optional_llm_direction",
            "final_formula": "0.45 * relatedness_score + 0.55 * direction_score",
        },
        "diagnostic_reason": "",
        "top_rejected_candidates": [],
        "is_acyclic": True,
    }
    if summary["total_module_concepts"] == 0:
        summary["diagnostic_reason"] = "no_concepts"
        return summary
    if len(concepts) == 0:
        summary["diagnostic_reason"] = "no_approved_concepts"
        return summary
    if len(concepts) < 2:
        summary["diagnostic_reason"] = "fewer_than_two_approved_concepts"
        return summary

    evidence = _build_evidence(concepts)
    candidate_scores = []
    rejected_candidates = []
    for source in concepts:
        for target in concepts:
            if source.id == target.id:
                continue
            source_evidence = evidence[source.id]
            target_evidence = evidence[target.id]
            if source.module_node_id != module.id or target.module_node_id != module.id:
                rejected_candidates.append(
                    _candidate_diag(
                        {
                            "source": source,
                            "target": target,
                            "score": 0.0,
                            "semantic_similarity": 0.0,
                            "dependency_cue_score": 0.0,
                            "pedagogical_relation_score": 0.0,
                            "source_order_score": 0.0,
                            "title_overlap_score": 0.0,
                            "relatedness_score": 0.0,
                            "direction_score": 0.0,
                        },
                        accepted=False,
                        rejection_reason="different_module",
                    )
                )
                continue
            if not _concept_has_evidence(source) or not _concept_has_evidence(target):
                # Still evaluate title/description, but note data quality in rejected diagnostics if it fails.
                pass
            source_order_score = _source_order_score(source_evidence, target_evidence)
            summary["candidate_pairs_evaluated"] += 1
            semantic_similarity = compute_concept_semantic_similarity(source_evidence.text, target_evidence.text)
            dependency_cue_score = _dependency_cue_score(source, target_evidence)
            pedagogical_relation_score = _pedagogical_relation_score(
                source,
                target,
                source_evidence.text,
                target_evidence.text,
            )
            title_overlap_score = _title_overlap_score(source, target)
            relatedness_score = _relatedness_score(semantic_similarity, title_overlap_score)
            direction_score = _direction_score(
                dependency_cue_score,
                pedagogical_relation_score,
                source_order_score,
            )
            score = _final_score(relatedness_score, direction_score)
            candidate = {
                "source": source,
                "target": target,
                "source_text": source_evidence.text,
                "target_text": target_evidence.text,
                "score": score,
                "semantic_similarity": semantic_similarity,
                "dependency_cue_score": dependency_cue_score,
                "pedagogical_relation_score": pedagogical_relation_score,
                "source_order_score": source_order_score,
                "title_overlap_score": title_overlap_score,
                "relatedness_score": relatedness_score,
                "direction_score": direction_score,
                "llm_direction_score": 0.0,
            }

            rejection_reason = ""
            if semantic_similarity < SCORE_CONFIG["minimum_semantic_similarity"]:
                rejection_reason = "insufficient_semantic_relationship"
                summary["edges_skipped_for_insufficient_semantic_relationship"] += 1
            elif dependency_cue_score <= 0 and pedagogical_relation_score <= 0 and source_order_score > 0:
                rejection_reason = "insufficient_directional_evidence"
                summary["edges_skipped_for_insufficient_direction"] += 1
                summary["edges_skipped_without_direction"] += 1
            elif source_order_score <= 0 and dependency_cue_score <= 0 and pedagogical_relation_score <= 0:
                rejection_reason = "reverse_order_without_direction"
                summary["edges_skipped_reverse_order_without_direction"] += 1
            elif direction_score < SCORE_CONFIG["minimum_direction_score"]:
                rejection_reason = "insufficient_directional_evidence"
                summary["edges_skipped_for_insufficient_direction"] += 1
                summary["edges_skipped_without_direction"] += 1
            elif score < threshold:
                rejection_reason = "below_threshold"
                summary["edges_skipped_below_threshold"] += 1

            if rejection_reason:
                rejected_candidates.append(_candidate_diag(candidate, accepted=False, rejection_reason=rejection_reason))
                continue
            candidate_scores.append(candidate)

    llm_scores = _optional_llm_direction_scores(candidate_scores)
    if llm_scores:
        rescored = []
        for candidate in candidate_scores:
            llm_score = llm_scores.get((candidate["source"].id, candidate["target"].id), 0.0)
            if llm_score:
                candidate = {
                    **candidate,
                    "llm_direction_score": llm_score,
                }
                candidate["direction_score"] = _direction_score(
                    candidate["dependency_cue_score"],
                    candidate["pedagogical_relation_score"],
                    candidate["source_order_score"],
                    llm_score,
                )
                candidate["score"] = _final_score(candidate["relatedness_score"], candidate["direction_score"])
            rescored.append(candidate)
        candidate_scores = [
            candidate
            for candidate in rescored
            if candidate["direction_score"] >= SCORE_CONFIG["minimum_direction_score"]
            and candidate["score"] >= threshold
        ]

    limited_candidates, removed_outgoing, removed_incoming = _limit_candidates(candidate_scores)
    summary["candidates_removed_by_outgoing_limit"] = removed_outgoing
    summary["candidates_removed_by_incoming_limit"] = removed_incoming
    generated_keys = set()
    accepted_edges: list[tuple[int, int]] = []

    with transaction.atomic():
        for candidate in sorted(limited_candidates, key=_sort_key):
            source = candidate["source"]
            target = candidate["target"]
            existing = ConceptPrerequisiteEdge.objects.filter(
                course=course,
                module_node=module,
                source=source,
                target=target,
            ).first()
            if existing and existing.validation_status == ConceptPrerequisiteEdge.ValidationStatus.REJECTED:
                summary["edges_skipped_rejected_by_teacher"] += 1
                rejected_candidates.append(_candidate_diag(candidate, accepted=False, rejection_reason="duplicate"))
                continue
            if edge_would_create_cycle(module, source.id, target.id, extra_edges=accepted_edges):
                summary["edges_rejected_for_cycle"] += 1
                rejected_candidates.append(_candidate_diag(candidate, accepted=False, rejection_reason="would_create_cycle"))
                continue
            if (
                concept_path_exists(module, source.id, target.id, extra_edges=accepted_edges)
                and candidate["dependency_cue_score"] < 0.90
                and candidate["pedagogical_relation_score"] < 0.85
            ):
                summary["edges_skipped_as_redundant"] += 1
                rejected_candidates.append(_candidate_diag(candidate, accepted=False, rejection_reason="redundant_transitive_edge"))
                continue

            generated_keys.add((source.id, target.id))
            accepted_edges.append((source.id, target.id))
            defaults = {
                "score": candidate["score"],
                "semantic_similarity": candidate["semantic_similarity"],
                "dependency_cue_score": candidate["dependency_cue_score"],
                "source_order_score": candidate["source_order_score"],
                "title_overlap_score": candidate["title_overlap_score"],
                "instructional_order_score": candidate["source_order_score"],
                "explanation": _candidate_explanation(candidate),
                "validation_status": ConceptPrerequisiteEdge.ValidationStatus.PENDING,
                "is_manual": False,
            }
            if existing:
                if existing.is_manual or existing.validation_status == ConceptPrerequisiteEdge.ValidationStatus.APPROVED:
                    summary["edges_skipped_duplicate"] += 1
                    continue
                for field, value in defaults.items():
                    setattr(existing, field, value)
                existing.full_clean()
                existing.save(
                    update_fields=[
                        "score",
                        "semantic_similarity",
                        "dependency_cue_score",
                        "source_order_score",
                        "title_overlap_score",
                        "instructional_order_score",
                        "explanation",
                        "validation_status",
                        "is_manual",
                        "updated_at",
                    ]
                )
                summary["edges_updated"] += 1
            else:
                edge = ConceptPrerequisiteEdge(
                    course=course,
                    module_node=module,
                    source=source,
                    target=target,
                    **defaults,
                )
                edge.full_clean()
                edge.save()
                summary["edges_created"] += 1

        stale_edges = ConceptPrerequisiteEdge.objects.filter(
            course=course,
            module_node=module,
            validation_status=ConceptPrerequisiteEdge.ValidationStatus.PENDING,
            is_manual=False,
        )
        stale_ids = [
            edge.id
            for edge in stale_edges
            if (edge.source_id, edge.target_id) not in generated_keys
        ]
        if stale_ids:
            summary["edges_deleted"] = ConceptPrerequisiteEdge.objects.filter(id__in=stale_ids).delete()[0]

        summary["is_acyclic"] = is_module_concept_dag_acyclic(module)
        if not summary["is_acyclic"]:
            raise ValidationError({"detail": "Generated concept DAG contains a cycle."})

        invalidate_module_dag(course, module, "Concept learner path was regenerated.")

    if not candidate_scores:
        if not any(row["source_count"] for row in summary["concept_diagnostics"]):
            summary["diagnostic_reason"] = "concepts_exist_but_have_no_source_evidence"
        elif summary["candidate_pairs_evaluated"] > 0:
            summary["diagnostic_reason"] = "candidate_pairs_failed_scoring_or_direction"
    elif summary["edges_rejected_for_cycle"] > 0 and summary["edges_created"] + summary["edges_updated"] == 0:
        summary["diagnostic_reason"] = "candidates_rejected_for_cycles"
    elif summary["edges_created"] + summary["edges_updated"] > 0:
        summary["diagnostic_reason"] = "edges_generated"
    summary["top_rejected_candidates"] = _top_rejected(rejected_candidates)
    return summary


def get_unlocked_concepts(module: OutlineNode, mastered_concept_ids: list[int]) -> list[ExtractedConcept]:
    mastered = {int(concept_id) for concept_id in mastered_concept_ids}
    approved_concepts = list(
        ExtractedConcept.objects.filter(
            module_node=module,
            validation_status=ExtractedConcept.ValidationStatus.APPROVED,
        ).order_by("order", "id")
    )
    approved_ids = {concept.id for concept in approved_concepts}
    incoming: dict[int, set[int]] = {concept.id: set() for concept in approved_concepts}
    for source_id, target_id in ConceptPrerequisiteEdge.objects.filter(
        module_node=module,
        validation_status=ConceptPrerequisiteEdge.ValidationStatus.APPROVED,
    ).values_list("source_id", "target_id"):
        if source_id in approved_ids and target_id in approved_ids:
            incoming.setdefault(target_id, set()).add(source_id)
    return [
        concept
        for concept in approved_concepts
        if concept.id not in mastered and incoming.get(concept.id, set()).issubset(mastered)
    ]
