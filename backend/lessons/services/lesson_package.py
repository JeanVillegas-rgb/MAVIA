"""Read-only content preview for the teacher UI.

Adapter for ``Milestone1-Jean/backend/course/services.py::LessonPackageService``
that reads mavia's own data instead of the ported ``course`` app: modules are
top-level ``OutlineNode``s, a lesson is a child topic, tracks come straight
from each material's ``generated_json["lesson_playlist"]``, and questions are
``lessons.Question`` rows.
"""

from lessons.models import LearningMaterial, OutlineNode, Question

from .audio_generator import _playlist_text_by_order


def _module_lesson_nodes(module_node):
    """Child topics of a module that have at least one completed material,
    in outline order."""
    descendant_ids = []
    frontier = [module_node]
    while frontier:
        node = frontier.pop(0)
        children = list(node.children.order_by("order", "id"))
        descendant_ids.extend(child.id for child in children)
        frontier.extend(children)

    nodes = (
        OutlineNode.objects.filter(id__in=descendant_ids)
        .order_by("depth", "order", "id")
    )
    return [
        node
        for node in nodes
        if LearningMaterial.objects.filter(
            outline_node=node, status=LearningMaterial.Status.COMPLETED
        ).exists()
    ]


def _lesson_tracks(lesson_node):
    """Ordered audio tracks for a topic, flattened across its materials."""
    materials = LearningMaterial.objects.filter(
        outline_node=lesson_node, status=LearningMaterial.Status.COMPLETED
    ).order_by("created_at", "id")

    tracks = []
    for material in materials:
        generated_json = material.generated_json or {}
        text_by_order = _playlist_text_by_order(material)
        for item in generated_json.get("lesson_playlist", []):
            narration_order = item.get("narration_item_order")
            text = item.get("narration") or item.get("text") or ""
            if narration_order is not None:
                text = text_by_order.get(int(narration_order), text)
            audio_url = item.get("audio_url") or ""
            tracks.append(
                {
                    "id": f"m{material.id}-{len(tracks)}",
                    "order": len(tracks),
                    "title": item.get("title") or f"Track {len(tracks) + 1}",
                    "type": item.get("type") or "lesson_content",
                    "audio_url": audio_url,
                    "audio_ready": bool(audio_url),
                    "text": text,
                }
            )
    return tracks


def _lesson_questions(lesson_node):
    questions = (
        Question.objects.filter(material__outline_node=lesson_node)
        .order_by("order", "id")
        .distinct()
    )
    payload = []
    for question in questions:
        payload.append(
            {
                "id": question.id,
                "order": question.order,
                "prompt": question.prompt,
                "question_type": question.question_type,
                "choices": question.choices or [],
                "correct_answer": question.correct_answer,
            }
        )
    return payload


def build_lesson_payload(lesson_node):
    tracks = _lesson_tracks(lesson_node)
    questions = _lesson_questions(lesson_node)
    return {
        "id": lesson_node.id,
        "title": lesson_node.title,
        "published": lesson_node.published,
        "tracks": tracks,
        "questions": questions,
        "has_questions": bool(questions),
        "track_count": len(tracks),
        "question_count": len(questions),
    }


def build_module_package(module_node):
    lesson_nodes = _module_lesson_nodes(module_node)
    lessons = [build_lesson_payload(node) for node in lesson_nodes]
    return {
        "module": {
            "id": module_node.id,
            "title": module_node.title,
            "order": module_node.order,
        },
        "lessons": lessons,
        "track_count": sum(lesson["track_count"] for lesson in lessons),
        "question_count": sum(lesson["question_count"] for lesson in lessons),
        "has_questions": any(lesson["has_questions"] for lesson in lessons),
    }


def build_course_module_summaries(course):
    modules = course.nodes.filter(parent__isnull=True).order_by("order", "id")
    summaries = []
    for module_node in modules:
        lesson_nodes = _module_lesson_nodes(module_node)
        track_count = 0
        question_count = 0
        for node in lesson_nodes:
            track_count += len(_lesson_tracks(node))
            question_count += Question.objects.filter(
                material__outline_node=node
            ).distinct().count()
        summaries.append(
            {
                "id": module_node.id,
                "title": module_node.title,
                "order": module_node.order,
                "published": module_node.published,
                "lesson_count": len(lesson_nodes),
                "track_count": track_count,
                "question_count": question_count,
            }
        )
    return summaries
