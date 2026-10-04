from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from lessons.models import CourseGroup, OutlineNode
from user.permissions import IsTeacherOrAdmin

from .services import build_topic_path, get_published_path
from .services.course_links import CourseLinkError, course_path, decide_course_link, refresh_course_links, restore_course_links
from .services.publishing import refresh_prerequisites
from .services.teacher_links import LinkError, add_link, decide_link, move_link, restore_links

# Ported from Milestone1-Jean (2026-09-15), where this review preview had no
# permission classes: that project's DRF default is AllowAny, so it and
# several `lessons`/`question_generation` teacher-authoring endpoints were
# open by the project's default rather than by a deliberate per-view choice.
# mavia's default is the opposite (IsAuthenticated app-wide, opt out per
# view) -- ported here as IsTeacherOrAdmin to match this file's own other
# actions, rather than carry the open behavior forward silently.
# The published path is different -- students read it -- so it requires a
# signed-in user.

ANSWER_VIEWERS = {"TEACHER", "ADMIN"}


@api_view(["GET"])
@permission_classes([IsTeacherOrAdmin])
def topic_learning_path(request, node_id):
    """The topic's learning path as the review screen previews it.

    ``paths`` stays a list so the client renders one path the way it rendered
    several, and a topic with no content is an empty list rather than a special
    case.
    """
    try:
        topic = OutlineNode.objects.get(pk=node_id)
    except OutlineNode.DoesNotExist:
        return Response({"detail": "Topic not found."}, status=status.HTTP_404_NOT_FOUND)

    # Links are derived on every open, not only at publish: v4's criteria
    # call no model (measured ~25 ms per topic), and a teacher must see the
    # links and recommendations before publishing makes them the students'.
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
    """A teacher says one concept needs another first.

    Body: ``{"prerequisite_concept_id": <group>, "dependent_concept_id": <group>}``.
    Returns the updated preview. Reaches students at the next publish.
    """
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
    """Make one concept the only prerequisite of another.

    Body: ``{"prerequisite_concept_id": <group>, "dependent_concept_id": <group>}``.
    """
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
    """Approve a suggestion, or reject (remove) a link. Body: ``{"status": "approved"|"rejected"}``."""
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
    """Undo a link change. Body: ``{"undo": [<record from the change's response>]}``."""
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
    """The path saved at the topic's last successful publish.

    Correct answers are included for teachers and admins only, so a student's
    device never receives the answer key. See ``learning_path/HANDOFF.md``.
    """
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


@api_view(["GET"])
@permission_classes([IsTeacherOrAdmin])
def course_learning_path(request, course_id):
    """The Course path page: topics in outline order and cross-topic arrows, derived on open."""
    course = CourseGroup.objects.filter(pk=course_id).first()
    if course is None:
        return Response({"detail": "Course not found."}, status=status.HTTP_404_NOT_FOUND)
    refresh_course_links(course)
    return Response(course_path(course))


@api_view(["POST"])
@permission_classes([IsTeacherOrAdmin])
def decide_course_path_link(request, course_id, link_id):
    """Approve a suggestion, or reject (remove) a course link. Body: ``{"status": "approved"|"rejected"}``."""
    course = CourseGroup.objects.filter(pk=course_id).first()
    if course is None:
        return Response({"detail": "Course not found."}, status=status.HTTP_404_NOT_FOUND)
    try:
        undo = decide_course_link(course, link_id, request.data.get("status"))
    except CourseLinkError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response({**course_path(course), "undo": undo})


@api_view(["POST"])
@permission_classes([IsTeacherOrAdmin])
def restore_course_path_links(request, course_id):
    """Undo a course link change. Body: ``{"undo": [<record from the change's response>]}``."""
    course = CourseGroup.objects.filter(pk=course_id).first()
    if course is None:
        return Response({"detail": "Course not found."}, status=status.HTTP_404_NOT_FOUND)
    try:
        restore_course_links(course, request.data.get("undo"))
    except CourseLinkError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(course_path(course))
