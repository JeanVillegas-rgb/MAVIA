"""Jean's average-learning-object SBERT topic matcher."""

from lessons.models import OutlineNode

from .embedding_service import SBERT_MODEL, embed_learning_objects


def score_learning_objects(material):
    subtopics = list(
        OutlineNode.objects.filter(
            course=material.course,
            depth=1,
            embedding_model=SBERT_MODEL,
        ).exclude(embedding=[])
    )
    results = []
    for obj in material.learning_objects.all():
        if not obj.embedding:
            continue
        scores = []
        for subtopic in subtopics:
            if len(obj.embedding) != len(subtopic.embedding):
                continue
            scores.append({
                "subtopic_id": subtopic.id,
                "subtopic_title": subtopic.title,
                "score": sum(a * b for a, b in zip(obj.embedding, subtopic.embedding)),
            })
        scores.sort(key=lambda item: item["score"], reverse=True)
        results.append({
            "draft_id": obj.draft_id,
            "object_title": obj.title,
            "subtopic_scores": scores,
        })
    return results


def _suggest_from_results(object_results):
    totals = {}
    for result in object_results:
        for match in result["subtopic_scores"]:
            item = totals.setdefault(match["subtopic_id"], {
                "subtopic_id": match["subtopic_id"],
                "subtopic_title": match["subtopic_title"],
                "score_sum": 0,
                "count": 0,
            })
            item["score_sum"] += match["score"]
            item["count"] += 1
    if not totals:
        return None
    ranked = sorted(
        ({
            "subtopic_id": item["subtopic_id"],
            "subtopic_title": item["subtopic_title"],
            "average_score": item["score_sum"] / item["count"],
        } for item in totals.values()),
        key=lambda item: item["average_score"],
        reverse=True,
    )
    return {
        "suggested_subtopic": ranked[0],
        "ranked_subtopics": ranked,
        "learning_object_results": object_results,
    }


def suggest_main_subtopic(material):
    return _suggest_from_results(score_learning_objects(material))


def suggest_main_topic(material, *, runtime_instance=None):
    """MAVIA-compatible name for Jean's stored-embedding matcher."""
    result = suggest_main_subtopic(material)
    if result is None:
        return None
    node = OutlineNode.objects.get(pk=result["suggested_subtopic"]["subtopic_id"])
    return {
        "suggested_topic": {
            **result["suggested_subtopic"],
            "outline_node": node,
            "outline_node_id": node.id,
            "outline_node_title": node.title,
        },
        "ranked_topics": result["ranked_subtopics"],
        "learning_object_results": result["learning_object_results"],
    }


def suggest_main_topic_for_drafts(course, draft_objects, *, runtime_instance=None):
    """Compatibility matcher for assessment-only documents before persistence."""
    embeddings = embed_learning_objects(draft_objects)
    subtopics = list(
        OutlineNode.objects.filter(
            course=course,
            depth=1,
            embedding_model=SBERT_MODEL,
        ).exclude(embedding=[])
    )
    object_results = []
    for draft in draft_objects:
        vector = embeddings.get(draft.get("draft_id"))
        if not vector:
            continue
        scores = [{
            "subtopic_id": node.id,
            "subtopic_title": node.title,
            "score": sum(a * b for a, b in zip(vector, node.embedding)),
        } for node in subtopics if len(vector) == len(node.embedding)]
        scores.sort(key=lambda item: item["score"], reverse=True)
        object_results.append({
            "draft_id": draft.get("draft_id"),
            "object_title": draft.get("title", ""),
            "subtopic_scores": scores,
        })
    result = _suggest_from_results(object_results)
    if result is None:
        return None
    node = OutlineNode.objects.get(pk=result["suggested_subtopic"]["subtopic_id"])
    return {
        "suggested_topic": {
            **result["suggested_subtopic"],
            "outline_node": node,
            "outline_node_id": node.id,
            "outline_node_title": node.title,
        },
        "ranked_topics": result["ranked_subtopics"],
        "learning_object_results": object_results,
    }


def assign_suggested_topic(material, *, runtime_instance=None):
    result = suggest_main_topic(material)
    if result is None:
        return None
    node = result["suggested_topic"]["outline_node"]
    material.outline_node = node
    ancestor = node
    while ancestor.parent_id is not None:
        ancestor = ancestor.parent
    material.module_node = ancestor
    material.save(update_fields=["outline_node", "module_node"])
    return result
