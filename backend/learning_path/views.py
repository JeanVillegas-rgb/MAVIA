from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from lessons.models import OutlineNode
from user.permissions import IsTeacherOrAdmin

from .services import build_topic_path, get_published_path
from .services.publishing import refresh_prerequisites
from .services.teacher_links import LinkError, add_link, decide_link, move_link, restore_links


ANSWER_VIEWERS = {"TEACHER", "ADMIN"}


@api_view(["GET"])
@permission_classes([IsTeacherOrAdmin])
def topic_learning_path(request, node_id):
    """GET: the topic's path preview; links are re-derived on every open."""
    try:
        topic = OutlineNode.objects.get(pk=node_id)
    except OutlineNode.DoesNotExist:
        return Response({"detail": "Topic not found."}, status=status.HTTP_404_NOT_FOUND)

    refresh_prerequisites(topic)
    return Response(_preview(topic))


def _preview(topic):
    path = build_topic_path(topic.id)
    return {
        "topic": {"id": topic.id, "title": topic.title},
        "paths": [path] if path["steps"] else [],
        "problems": [],
    }


@api_view(["POST"])
@permission_classes([IsTeacherOrAdmin])
def add_path_link(request, node_id):
    """POST: a teacher says one concept needs another first."""
    topic = OutlineNode.objects.filter(pk=node_id).first()
    if topic is None:
        return Response({"detail": "Topic not found."}, status=status.HTTP_404_NOT_FOUND)
    try:
        undo = add_link(
            topic,
            int(request.data.get("prerequisite_concept_id")),
            int(request.data.get("dependent_concept_id")),
        )
    except (TypeError, ValueError) as exc:
        detail = str(exc) if isinstance(exc, LinkError) else "Choose both concepts."
        return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)
    return Response({**_preview(topic), "undo": undo}, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsTeacherOrAdmin])
def move_path_link(request, node_id):
    """POST: make one concept the only prerequisite of another."""
    topic = OutlineNode.objects.filter(pk=node_id).first()
    if topic is None:
        return Response({"detail": "Topic not found."}, status=status.HTTP_404_NOT_FOUND)
    try:
        undo = move_link(
            topic,
            int(request.data.get("prerequisite_concept_id")),
            int(request.data.get("dependent_concept_id")),
        )
    except (TypeError, ValueError) as exc:
        detail = str(exc) if isinstance(exc, LinkError) else "Choose both concepts."
        return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)
    return Response({**_preview(topic), "undo": undo})


@api_view(["POST"])
@permission_classes([IsTeacherOrAdmin])
def decide_path_link(request, node_id, link_id):
    """POST: approve or reject a link."""
    topic = OutlineNode.objects.filter(pk=node_id).first()
    if topic is None:
        return Response({"detail": "Topic not found."}, status=status.HTTP_404_NOT_FOUND)
    try:
        undo = decide_link(topic, link_id, request.data.get("status"))
    except LinkError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response({**_preview(topic), "undo": undo})


@api_view(["POST"])
@permission_classes([IsTeacherOrAdmin])
def restore_path_links(request, node_id):
    """POST: undo a link change."""
    topic = OutlineNode.objects.filter(pk=node_id).first()
    if topic is None:
        return Response({"detail": "Topic not found."}, status=status.HTTP_404_NOT_FOUND)
    try:
        restore_links(topic, request.data.get("undo"))
    except LinkError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(_preview(topic))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def published_learning_path(request, node_id):
    """GET: the saved path; answers are included for teachers and admins only."""
    try:
        topic = OutlineNode.objects.get(pk=node_id)
    except OutlineNode.DoesNotExist:
        return Response({"detail": "Topic not found."}, status=status.HTTP_404_NOT_FOUND)

    include_answers = getattr(request.user, "role", None) in ANSWER_VIEWERS
    path = get_published_path(topic, include_answers=include_answers)
    if path is None:
        return Response(
            {"detail": "This topic has no published learning path yet. Publish it first."},
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response(path)
