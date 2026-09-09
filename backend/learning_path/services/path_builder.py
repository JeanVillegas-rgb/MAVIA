"""Build the generic (pre-adaptive) learning path for one learning material.

This is the deterministic scaffold: every student sees this same order. The
adaptive layer will later reorder, skip, or repeat within it, but it does not
exist yet and nothing here is student-specific.
"""

from lessons.models import LearningMaterial

from ..models import PrerequisiteEdge
from .edge_derivation import learning_objects_for_material, rebuild_edges_for_material
from .text_signals import build_first_mention_ranks
from .topological_sort import kahn_topological_order, mean_incoming_confidence


def _edge_rows_for(learning_objects):
    ids = [lo.id for lo in learning_objects]
    return list(
        PrerequisiteEdge.objects.filter(dependent_id__in=ids, prerequisite_id__in=ids)
        .order_by("prerequisite_id", "dependent_id", "id")
    )


def _edges_and_weights(edge_rows):
    """Collapse rows to unique pairs, keeping each pair's strongest evidence.

    One pair can be supported by several signals. The sort should see the best
    reason that pair exists, not an arbitrary one, so the pair's confidence is
    the maximum across its supporting rows.
    """
    pairs, weights = [], {}
    for row in edge_rows:
        pair = (row.prerequisite_id, row.dependent_id)
        if pair not in weights:
            pairs.append(pair)
            weights[pair] = row.weight
        else:
            weights[pair] = max(weights[pair], row.weight)
    return pairs, weights


def build_learning_path(material_id, *, rebuild=False):
    """Return the ordered learning objects for a material plus its diagnostics.

    Set ``rebuild=True`` to re-derive the prerequisite edges first. Edges are
    also derived automatically when the material has none yet, so a caller never
    has to remember to run derivation before asking for a path.
    """
    material = LearningMaterial.objects.get(pk=material_id)
    learning_objects = learning_objects_for_material(material_id)
    node_ids = [lo.id for lo in learning_objects]

    if rebuild or not _edge_rows_for(learning_objects):
        rebuild_edges_for_material(material_id)

    edge_rows = _edge_rows_for(learning_objects)
    edge_pairs, edge_weights = _edges_and_weights(edge_rows)
    ranks = build_first_mention_ranks(learning_objects)

    ordered_ids, depth_by_id = kahn_topological_order(
        node_ids,
        edge_pairs,
        first_mention_rank=ranks,
        edge_weights=edge_weights,
    )
    confidence_by_id = mean_incoming_confidence(node_ids, edge_pairs, edge_weights)

    prerequisites_by_id = {node_id: [] for node_id in node_ids}
    for prerequisite_id, dependent_id in edge_pairs:
        prerequisites_by_id[dependent_id].append(prerequisite_id)

    object_by_id = {lo.id: lo for lo in learning_objects}
    source_order_ids = node_ids  # already sorted by (order, id)

    steps = []
    for position, node_id in enumerate(ordered_ids, start=1):
        learning_object = object_by_id[node_id]
        steps.append({
            "position": position,
            "learning_object_id": node_id,
            "title": learning_object.title,
            "section_title": learning_object.section_title,
            "kind": learning_object.kind,
            "dag_depth": depth_by_id.get(node_id, 0),
            "prerequisite_ids": sorted(prerequisites_by_id[node_id]),
            "prerequisite_count": len(prerequisites_by_id[node_id]),
            "first_mention_rank": ranks[node_id],
            "source_order": learning_object.order,
            "support_confidence": round(confidence_by_id.get(node_id, 1.0), 3),
        })

    object_titles = {lo.id: lo.title for lo in learning_objects}
    edges = [
        {
            "id": row.id,
            "prerequisite_id": row.prerequisite_id,
            "prerequisite_title": object_titles.get(row.prerequisite_id, ""),
            "dependent_id": row.dependent_id,
            "dependent_title": object_titles.get(row.dependent_id, ""),
            "signal": row.signal,
            "signal_label": row.get_signal_display(),
            "source": row.source,
            "weight": row.weight,
            "evidence": row.evidence,
        }
        for row in edge_rows
    ]

    return {
        "material_id": material.id,
        "material_title": material.title,
        "learning_path": ordered_ids,
        "steps": steps,
        "edges": edges,
        "diagnostics": {
            "node_count": len(node_ids),
            "edge_count": len(edge_pairs),
            "root_count": sum(1 for step in steps if step["prerequisite_count"] == 0),
            "max_depth": max(depth_by_id.values(), default=0),
            "source_order": source_order_ids,
            "matches_source_order": ordered_ids == source_order_ids,
            "displaced_object_count": sum(
                1 for a, b in zip(ordered_ids, source_order_ids) if a != b
            ),
        },
    }
