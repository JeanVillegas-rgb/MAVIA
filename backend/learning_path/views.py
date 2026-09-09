from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from lessons.models import LearningMaterial, LearningObject, OutlineNode

from .models import PrerequisiteEdge
from .services import GraphCycleError, build_learning_path
from .services.edge_derivation import TEACHER_EDGE_WEIGHT

# No permission classes, deliberately: these are teacher-authoring endpoints and
# they sit alongside `lessons` and `question_generation`, which are also open.
# Gating only this app made the review screen unreachable, since the rest of the
# authoring flow never asks anyone to sign in. (`course` and `adaptive` ARE
# gated — those are the learner-facing APIs.) Auth should be applied across all
# teacher endpoints at once, not one app at a time.


@api_view(["GET", "POST"])
def material_learning_path(request, material_id):
    """GET returns the material's generic learning path; POST re-derives it."""
    try:
        payload = build_learning_path(material_id, rebuild=request.method == "POST")
    except LearningMaterial.DoesNotExist:
        return Response({"detail": "Learning material not found."}, status=status.HTTP_404_NOT_FOUND)
    except GraphCycleError as exc:
        return Response(
            {"detail": str(exc), "unresolved_learning_object_ids": exc.unresolved_nodes},
            status=status.HTTP_409_CONFLICT,
        )
    return Response(payload)


@api_view(["GET"])
def topic_learning_path(request, node_id):
    """Every learning path under one outline topic, for teacher review.

    A topic can hold several uploaded PDFs, and each is ordered independently
    for now, so this returns one path per material rather than pretending they
    are already a single sequence.
    """
    try:
        topic = OutlineNode.objects.get(pk=node_id)
    except OutlineNode.DoesNotExist:
        return Response({"detail": "Topic not found."}, status=status.HTTP_404_NOT_FOUND)

    materials = LearningMaterial.objects.filter(
        outline_node=topic, status="completed"
    ).order_by("created_at", "id")

    paths, problems = [], []
    for material in materials:
        try:
            paths.append(build_learning_path(material.id))
        except GraphCycleError as exc:
            problems.append({
                "material_id": material.id,
                "material_title": material.title,
                "detail": str(exc),
                "unresolved_learning_object_ids": exc.unresolved_nodes,
            })

    return Response({
        "topic": {"id": topic.id, "title": topic.title},
        "paths": paths,
        "problems": problems,
    })


@api_view(["POST"])
def create_edge(request):
    """Add a teacher-authored prerequisite.

    Body: {"prerequisite_id": <learning object>, "dependent_id": <learning object>}

    The edge is rejected if it would make the graph cyclic — the teacher is
    told which objects are caught in the loop rather than being left with a
    path that cannot be produced.
    """
    try:
        prerequisite_id = int(request.data.get("prerequisite_id"))
        dependent_id = int(request.data.get("dependent_id"))
    except (TypeError, ValueError):
        return Response(
            {"detail": "prerequisite_id and dependent_id are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if prerequisite_id == dependent_id:
        return Response(
            {"detail": "A learning object cannot be its own prerequisite."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    objects = LearningObject.objects.filter(id__in=[prerequisite_id, dependent_id])
    by_id = {obj.id: obj for obj in objects}
    if len(by_id) != 2:
        return Response({"detail": "Learning object not found."}, status=status.HTTP_404_NOT_FOUND)

    prerequisite, dependent = by_id[prerequisite_id], by_id[dependent_id]
    if prerequisite.material_id != dependent.material_id:
        return Response(
            {"detail": "Both learning objects must belong to the same material."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    edge, created = PrerequisiteEdge.objects.get_or_create(
        prerequisite=prerequisite,
        dependent=dependent,
        signal=PrerequisiteEdge.Signal.TEACHER_AUTHORED,
        defaults={
            "source": PrerequisiteEdge.Source.TEACHER,
            "weight": TEACHER_EDGE_WEIGHT,
            "evidence": {"added_by": "teacher"},
        },
    )

    try:
        payload = build_learning_path(dependent.material_id)
    except GraphCycleError as exc:
        # Roll the edge back: keeping it would leave this material with no
        # producible path at all.
        if created:
            edge.delete()
        return Response(
            {
                "detail": (
                    "That prerequisite would create a loop, so it was not saved. "
                    + str(exc)
                ),
                "unresolved_learning_object_ids": exc.unresolved_nodes,
            },
            status=status.HTTP_409_CONFLICT,
        )

    return Response(
        payload,
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@api_view(["DELETE"])
def delete_edge(request, edge_id):
    """Remove one prerequisite and return the material's re-ordered path.

    Derived edges can be removed too — a teacher overruling the derivation is
    the point of this screen. A later re-derivation will propose it again,
    which is why removals are best paired with the review step rather than
    treated as permanent.
    """
    try:
        edge = PrerequisiteEdge.objects.select_related("dependent").get(pk=edge_id)
    except PrerequisiteEdge.DoesNotExist:
        return Response({"detail": "Prerequisite not found."}, status=status.HTTP_404_NOT_FOUND)

    material_id = edge.dependent.material_id
    edge.delete()
    return Response(build_learning_path(material_id))
