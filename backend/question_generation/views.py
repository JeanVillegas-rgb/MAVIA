import logging
import threading

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from lessons.models import LearningMaterial
from course.version_assignment import assign_group_versions

from .models import GenerationEvent, GenerationRun

logger = logging.getLogger(__name__)


STALE_RUN_SECONDS = 300


def _standard_question_source(node):
    """The concept's one question source, or an error response."""
    if node.group_id is None:
        return node, ""
    state = assign_group_versions(node.group)
    if not state.get("classification_complete") or not state.get("original_selected"):
        return None, "Classify this concept's PDF variants before generating questions."
    standard = node.group.learning_objects.filter(
        pk=state["representative_id"],
    ).first()
    if standard is None or not (standard.content or "").strip():
        return None, "This concept has no usable Standard version."
    return standard, ""


def _event_recorder(run_id):
    """A trace callback that stores each event under run_id."""
    seq_counter = [0]

    def on_event(event_type, message, data):
        seq_counter[0] += 1
        GenerationEvent.objects.create(
            run_id=run_id,
            seq=seq_counter[0],
            event_type=event_type,
            message=message,
            data=data,
        )
    return on_event


def _run_pipeline(run_id, node_id, append=False):
    """Thread target: one concept's generation, traced to the DB."""
    from .services.pipeline import generate_questions_for_material

    on_event = _event_recorder(run_id)

    try:
        run = GenerationRun.objects.select_related("material").get(id=run_id)
        questions = generate_questions_for_material(
            run.material, [node_id], on_event=on_event, append=append,
        )
        on_event("saved", f"Saved {len(questions)} questions to database",
                 {"count": len(questions)})
        GenerationRun.objects.filter(id=run_id).update(
            status="finished", finished_at=timezone.now())
    except Exception as e:
        logger.exception("[Questions] run %s failed: %s: %s", run_id, type(e).__name__, e)
        on_event("error", f"{type(e).__name__}: {e}", None)
        GenerationRun.objects.filter(id=run_id).update(
            status="failed", finished_at=timezone.now())


def _question_run_in_progress():
    """A 409 response while another question run is live, else None."""
    for run in GenerationRun.objects.filter(
        status="running",
        kind=GenerationRun.Kind.QUESTIONS,
    ):
        last_event = run.events.order_by("-seq").first()
        last_activity = last_event.created_at if last_event else run.started_at
        if (timezone.now() - last_activity).total_seconds() > STALE_RUN_SECONDS:
            run.status = "failed"
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at"])
        else:
            return Response(
                {"error": "A generation run is already in progress", "run_id": run.id},
                status=status.HTTP_409_CONFLICT,
            )
    return None


def _run_topic_pipeline(run_id, node_ids):
    """Thread target: topic generation in rounds, traced to the DB."""
    from lessons.models import LearningObject
    from .services.pipeline import generate_questions_for_topic

    on_event = _event_recorder(run_id)
    try:
        run = GenerationRun.objects.select_related("outline_node").get(id=run_id)
        by_id = LearningObject.objects.select_related("group", "material").in_bulk(node_ids)
        result = generate_questions_for_topic(
            run.outline_node, [by_id[pk] for pk in node_ids if pk in by_id], on_event=on_event,
        )
        on_event("saved", f"Saved {result['added']} questions to database", result)
        GenerationRun.objects.filter(id=run_id).update(
            status="finished", finished_at=timezone.now())
    except Exception as e:
        logger.exception("[Questions] run %s failed: %s: %s", run_id, type(e).__name__, e)
        on_event("error", f"{type(e).__name__}: {e}", None)
        GenerationRun.objects.filter(id=run_id).update(
            status="failed", finished_at=timezone.now())


class StartTopicGenerationView(APIView):
    """POST /api/generation/topics/<id>/start/: top up every short concept in the background."""
    def post(self, request, outline_node_id):
        from lessons.models import LearningObjectGroup, OutlineNode

        outline_node = OutlineNode.objects.filter(pk=outline_node_id).first()
        if outline_node is None:
            return Response({"error": "Topic not found"}, status=status.HTTP_404_NOT_FOUND)

        node_ids = []
        groups = LearningObjectGroup.objects.filter(outline_node=outline_node).order_by("id")
        for group in groups:
            member = group.learning_objects.filter(
                material__generated_json__learning_objects_confirmed=True,
            ).exclude(content="").order_by("material_id", "order", "id").first()
            if member is None:
                continue
            standard, standard_error = _standard_question_source(member)
            if standard_error:
                continue
            node_ids.append(standard.id)
        if not node_ids:
            return Response(
                {"error": "This topic has no concept with a Standard version to generate from."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        busy = _question_run_in_progress()
        if busy is not None:
            return busy

        run = GenerationRun.objects.create(
            outline_node=outline_node, kind=GenerationRun.Kind.QUESTIONS,
        )
        threading.Thread(
            target=_run_topic_pipeline, args=(run.id, node_ids), daemon=True,
        ).start()
        return Response(
            {"run_id": run.id, "concept_count": len(node_ids)},
            status=status.HTTP_201_CREATED,
        )


class StartGenerationView(APIView):
    """POST /api/generation/materials/<id>/nodes/<id>/start/: generate for one concept."""
    def post(self, request, material_id, node_id):
        try:
            material = LearningMaterial.objects.get(id=material_id)
        except LearningMaterial.DoesNotExist:
            return Response({"error": "Material not found"},
                            status=status.HTTP_404_NOT_FOUND)

        if material.status != LearningMaterial.Status.COMPLETED:
            return Response(
                {"error": f"Material is not completed (status: {material.status})"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        content_nodes = material.learning_objects.exclude(content="")
        if not content_nodes.exists():
            return Response(
                {"error": "Material has no narrated learning objects to generate from"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        append = request.data.get("append") is True
        node = content_nodes.filter(id=node_id).first()
        if node is None:
            return Response(
                {"error": "Learning object not found for this material "
                          "(or it has no narration content)"},
                status=status.HTTP_404_NOT_FOUND,
            )
        standard, standard_error = _standard_question_source(node)
        if standard_error:
            return Response({"error": standard_error}, status=status.HTTP_409_CONFLICT)
        if standard.id != node.id:
            return Response(
                {"error": "Questions can only be generated from this concept's Standard version.",
                 "standard_learning_object_id": standard.id},
                status=status.HTTP_400_BAD_REQUEST,
            )

        busy = _question_run_in_progress()
        if busy is not None:
            return busy

        run = GenerationRun.objects.create(
            material=material, node=node, kind=GenerationRun.Kind.QUESTIONS,
        )
        threading.Thread(
            target=_run_pipeline,
            args=(run.id, node.id),
            kwargs={"append": append},
            daemon=True,
        ).start()
        return Response(
            {"run_id": run.id, "node_id": node.id},
            status=status.HTTP_201_CREATED,
        )


class GenerationRunsView(APIView):
    """GET /api/generation/runs/: recent runs, newest first."""
    def get(self, request):
        runs = GenerationRun.objects.select_related("material", "node").order_by("-started_at")
        material_id = request.query_params.get("material_id")
        if material_id:
            runs = runs.filter(material_id=material_id)
        return Response([
            {
                "id": r.id,
                "material_id": r.material_id,
                "material_title": r.material.title if r.material_id else None,
                "node_id": r.node_id,
                "node_title": r.node.title if r.node else None,
                "status": r.status,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
            }
            for r in runs[:20]
        ])


class GenerationTraceView(APIView):
    """GET /api/generation/runs/<id>/events/?after=<seq>: run status and newer events."""
    def get(self, request, run_id):
        try:
            run = GenerationRun.objects.select_related(
                "material", "node", "outline_node"
            ).get(id=run_id)
        except GenerationRun.DoesNotExist:
            return Response({"error": "Run not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            after = int(request.query_params.get("after", 0))
        except ValueError:
            after = 0

        events = run.events.filter(seq__gt=after)
        return Response({
            "run": {
                "id": run.id,
                "kind": run.kind,
                "kind_label": run.get_kind_display(),
                "material_id": run.material_id,
                "material_title": run.material.title if run.material_id else None,
                "node_id": run.node_id,
                "node_title": run.node.title if run.node else None,
                "outline_node_id": run.outline_node_id,
                "outline_node_title": run.outline_node.title if run.outline_node_id else None,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            },
            "events": [
                {
                    "seq": e.seq,
                    "event_type": e.event_type,
                    "message": e.message,
                    "data": e.data,
                    "created_at": e.created_at,
                }
                for e in events
            ],
        })
