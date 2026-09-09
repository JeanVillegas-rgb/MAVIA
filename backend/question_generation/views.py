import threading

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from lessons.models import LearningMaterial, LearningObject
from lessons.services.audio_generator import mark_material_audio_stale

from .models import GeneratedQuestion, GenerationEvent, GenerationRun, LearnerResponse
from .serializers import QuestionSerializer
from .services.bloom_classifier import BLOOM_TO_DIFFICULTY
from .services.pipeline import (
    UNASSESSABLE_BLOOM_LEVELS,
    get_classifier,
    is_node_complete,
    node_question_status,
)


class GetQuestionView(APIView):
    """
    GET /api/questions/?node_id=<learning_object_id>&thinking_order=LOT&learner_id=learner_123

    Returns an unanswered question for this learner at the requested
    thinking order (LOT or HOT).
    """
    def get(self, request):
        node_id = request.query_params.get("node_id")
        thinking_order = request.query_params.get("thinking_order", "LOT")
        learner_id = request.query_params.get("learner_id")

        if not node_id or not learner_id:
            return Response(
                {"error": "node_id and learner_id required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # A node whose pools are short is withheld: there would be no alternate
        # question to offer after a wrong answer, so the checkpoint could not be
        # retried without repeating the question the learner just saw.
        try:
            node = LearningObject.objects.get(id=node_id)
        except LearningObject.DoesNotExist:
            return Response({"error": "Content node not found"},
                            status=status.HTTP_404_NOT_FOUND)
        if not is_node_complete(node):
            return Response(
                {"message": "This lesson is not ready yet — its question pool is incomplete."},
                status=status.HTTP_409_CONFLICT,
            )

        answered_ids = LearnerResponse.objects.filter(
            learner_id=learner_id,
            question__node_id=node_id,
        ).values_list("question_id", flat=True)

        question = (
            GeneratedQuestion.objects
            .filter(node_id=node_id, thinking_order=thinking_order, status="final")
            .exclude(id__in=answered_ids)
            .order_by("?")
            .first()
        )

        if not question:
            return Response(
                {"message": "No more questions at this thinking order"},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response(QuestionSerializer(question).data)


class SubmitAnswerView(APIView):
    """
    POST /api/questions/submit/
    Body: {"question_id": 1, "selected_answer": "B", "learner_id": "learner_123"}
    """
    def post(self, request):
        question_id = request.data.get("question_id")
        selected = request.data.get("selected_answer")
        learner_id = request.data.get("learner_id")

        if not question_id or selected is None or not learner_id:
            return Response(
                {"error": "question_id, selected_answer and learner_id required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            question = GeneratedQuestion.objects.get(id=question_id)
        except GeneratedQuestion.DoesNotExist:
            return Response(
                {"error": "Question not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        is_correct = selected == question.correct_answer

        LearnerResponse.objects.create(
            learner_id=learner_id,
            question=question,
            selected_answer=selected,
            is_correct=is_correct,
        )

        return Response({
            "is_correct": is_correct,
            "correct_answer": question.correct_answer,
            "explanation": question.explanation,
        })


class QuestionStatsView(APIView):
    """
    GET /api/questions/stats/?node_id=<learning_object_id>&learner_id=learner_123

    Per-node progress stats for a learner, broken down by thinking order.
    """
    def get(self, request):
        node_id = request.query_params.get("node_id")
        learner_id = request.query_params.get("learner_id")

        if not node_id or not learner_id:
            return Response(
                {"error": "node_id and learner_id required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        questions = GeneratedQuestion.objects.filter(node_id=node_id, status="final")
        responses = LearnerResponse.objects.filter(
            learner_id=learner_id,
            question__node_id=node_id,
        )

        by_thinking_order = {}
        for order in ("LOT", "HOT"):
            order_responses = responses.filter(question__thinking_order=order)
            by_thinking_order[order] = {
                "total": questions.filter(thinking_order=order).count(),
                "answered": order_responses.values("question_id").distinct().count(),
                "correct": order_responses.filter(is_correct=True)
                    .values("question_id").distinct().count(),
            }

        return Response({
            "total": sum(d["total"] for d in by_thinking_order.values()),
            "answered": sum(d["answered"] for d in by_thinking_order.values()),
            "correct": sum(d["correct"] for d in by_thinking_order.values()),
            "by_thinking_order": by_thinking_order,
        })


# ── Generation pipeline (teacher-facing) ──

# If a "running" run has emitted nothing for this long, assume the server
# restarted mid-run and the thread is gone.
STALE_RUN_SECONDS = 300


def _run_pipeline(run_id, material_id, node_ids=None):
    """Thread target: run the pipeline, streaming trace events to the DB."""
    from .services.pipeline import generate_questions_for_material

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

    try:
        material = LearningMaterial.objects.get(id=material_id)
        # questions are saved to the DB per node as the pipeline progresses
        questions = generate_questions_for_material(
            material, on_event=on_event, node_ids=node_ids)
        on_event("saved", f"Saved {len(questions)} questions to database",
                 {"count": len(questions)})
        GenerationRun.objects.filter(id=run_id).update(
            status="finished", finished_at=timezone.now())
    except Exception as e:  # noqa: BLE001 — surface anything to the trace
        on_event("error", f"{type(e).__name__}: {e}", None)
        GenerationRun.objects.filter(id=run_id).update(
            status="failed", finished_at=timezone.now())


class StartGenerationView(APIView):
    """
    POST /api/generation/materials/<material_id>/start/
    Body (optional): {"node_id": <learning_object_id>}

    Teacher-triggered: generate questions for every text learning object of
    a completed material, or for a single learning object when node_id is
    given. Runs in the background; poll the trace endpoint.
    """
    def post(self, request, material_id, node_id=None):
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

        text_nodes = material.learning_objects.filter(kind="text").exclude(content="")
        if not text_nodes.exists():
            return Response(
                {"error": "Material has no text learning objects to generate from"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        node = None
        requested_node_id = node_id if node_id is not None else request.data.get("node_id")
        if requested_node_id is not None:
            node = text_nodes.filter(id=requested_node_id).first()
            if node is None:
                return Response(
                    {"error": "Learning object not found for this material "
                              "(or it has no text content)"},
                    status=status.HTTP_404_NOT_FOUND,
                )

        # one run at a time; unstick runs orphaned by a server restart
        for run in GenerationRun.objects.filter(status="running"):
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

        run = GenerationRun.objects.create(material=material, node=node)
        threading.Thread(
            target=_run_pipeline,
            args=(run.id, material.id, [node.id] if node else None),
            daemon=True,
        ).start()
        return Response(
            {"run_id": run.id, "node_id": node.id if node else None},
            status=status.HTTP_201_CREATED,
        )


class MaterialQuestionsView(APIView):
    """
    GET /api/generation/materials/<material_id>/questions/

    Teacher-facing review of the stored question bank, grouped per learning
    object. Includes correct answers — never expose this to learners.
    """
    THINKING_ORDER_RANK = {"LOT": 0, "HOT": 1}

    def get(self, request, material_id):
        try:
            material = LearningMaterial.objects.get(id=material_id)
        except LearningMaterial.DoesNotExist:
            return Response({"error": "Material not found"},
                            status=status.HTTP_404_NOT_FOUND)

        nodes = material.learning_objects.filter(kind="text").order_by("order", "id")
        payload = []
        for node in nodes:
            questions = sorted(
                node.generated_questions.filter(status="final"),
                key=lambda q: (self.THINKING_ORDER_RANK.get(q.thinking_order, 2), q.id),
            )
            payload.append({
                "node_id": node.id,
                "node_title": node.title,
                "questions": [
                    {
                        "id": q.id,
                        "question_text": q.question_text,
                        "question_format": q.question_format,
                        "choices": q.choices,
                        "correct_answer": q.correct_answer,
                        "explanation": q.explanation,
                        "thinking_order": q.thinking_order,
                        "difficulty": q.difficulty,
                        "bloom_level": q.bloom_level,
                        "category": q.category,
                    }
                    for q in questions
                ],
            })
        return Response(payload)


class NodeQuestionsView(APIView):
    """
    GET  /api/generation/nodes/<node_id>/questions/
        Pool status for one content node: how full each band is, whether the
        node may be delivered to a learner, and the questions it holds.

    POST /api/generation/nodes/<node_id>/questions/
        Body: {"question_text", "question_format": "MCQ"|"TF",
               "choices": {...}, "correct_answer", "explanation"?}

        Teacher-authored question. This is how a node whose pools came up short
        is repaired: the pipeline never regenerates to fill a gap, so without
        this the node would stay incomplete and withheld forever.

        The Bloom level is NOT taken from the request. It is assigned by the
        classifier, exactly as it is for generated questions — the classifier
        stays the single authority on what a question actually is.
    """

    def _node_or_404(self, node_id):
        try:
            return LearningObject.objects.get(id=node_id), None
        except LearningObject.DoesNotExist:
            return None, Response(
                {"error": "Content node not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

    def get(self, request, node_id):
        node, error = self._node_or_404(node_id)
        if error:
            return error

        questions = GeneratedQuestion.objects.filter(
            node=node, status="final"
        ).order_by("thinking_order", "id")
        payload = node_question_status(node)
        payload["questions"] = QuestionSerializer(questions, many=True).data
        return Response(payload)

    def post(self, request, node_id):
        node, error = self._node_or_404(node_id)
        if error:
            return error

        data = request.data
        question_text = str(data.get("question_text", "")).strip()
        question_format = str(data.get("question_format", "")).strip().upper()
        correct_answer = str(data.get("correct_answer", "")).strip()

        if not question_text:
            return Response({"error": "question_text is required"},
                            status=status.HTTP_400_BAD_REQUEST)
        if question_format not in {"MCQ", "TF"}:
            return Response({"error": "question_format must be MCQ or TF"},
                            status=status.HTTP_400_BAD_REQUEST)
        if not correct_answer:
            return Response({"error": "correct_answer is required"},
                            status=status.HTTP_400_BAD_REQUEST)

        choices = data.get("choices")
        if question_format == "MCQ":
            if not isinstance(choices, dict) or len(choices) < 2:
                return Response({"error": "MCQ questions need a choices object"},
                                status=status.HTTP_400_BAD_REQUEST)
            if correct_answer not in choices:
                return Response({"error": "correct_answer must be one of the choice keys"},
                                status=status.HTTP_400_BAD_REQUEST)
        else:
            choices = None
            if correct_answer.lower() not in {"true", "false"}:
                return Response({"error": "correct_answer must be True or False"},
                                status=status.HTTP_400_BAD_REQUEST)
            correct_answer = correct_answer.capitalize()

        classification = get_classifier().classify(question_text)
        bloom_level = classification["bloom_level"]
        thinking_order = classification["thinking_order"]

        if bloom_level in UNASSESSABLE_BLOOM_LEVELS or thinking_order is None:
            return Response(
                {
                    "error": (
                        f"This reads as a {bloom_level}-level question, which cannot be "
                        f"assessed by multiple choice or true/false. Rephrase it to ask "
                        f"for a single defensible answer."
                    ),
                    "bloom_level": bloom_level,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        question = GeneratedQuestion.objects.create(
            node=node,
            question_text=question_text,
            question_format=question_format,
            choices=choices,
            correct_answer=correct_answer,
            explanation=str(data.get("explanation", "")).strip(),
            bloom_level=bloom_level,
            thinking_order=thinking_order,
            difficulty=classification.get("difficulty")
            or BLOOM_TO_DIFFICULTY.get(bloom_level, ""),
            category=classification["category"],
            status="final",
        )
        mark_material_audio_stale(node.material)

        payload = node_question_status(node)
        payload["question"] = QuestionSerializer(question).data
        return Response(payload, status=status.HTTP_201_CREATED)


class QuestionDetailView(APIView):
    """
    PATCH /api/generation/questions/<question_id>/
    Body: any of {"question_text", "choices", "correct_answer", "explanation"}

    Teacher review edits to a stored question. DELETE removes the question.
    NOTE: editing or deleting cascades no learner responses except on delete.
    """
    def patch(self, request, question_id):
        try:
            question = GeneratedQuestion.objects.get(id=question_id)
        except GeneratedQuestion.DoesNotExist:
            return Response({"error": "Question not found"},
                            status=status.HTTP_404_NOT_FOUND)

        data = request.data

        if "question_text" in data:
            text = str(data["question_text"]).strip()
            if not text:
                return Response({"error": "Question text cannot be blank"},
                                status=status.HTTP_400_BAD_REQUEST)
            question.question_text = text

        if "choices" in data:
            if question.question_format != "MCQ":
                return Response({"error": "Only MCQ questions have choices"},
                                status=status.HTTP_400_BAD_REQUEST)
            choices = data["choices"]
            if not isinstance(choices, dict) or len(choices) < 2:
                return Response({"error": "Choices must be an object with at least two options"},
                                status=status.HTTP_400_BAD_REQUEST)
            cleaned = {str(k).strip().upper(): str(v).strip() for k, v in choices.items()}
            if any(not v for v in cleaned.values()):
                return Response({"error": "Choice text cannot be blank"},
                                status=status.HTTP_400_BAD_REQUEST)
            question.choices = cleaned

        if "correct_answer" in data:
            question.correct_answer = str(data["correct_answer"]).strip()

        if "explanation" in data:
            question.explanation = str(data["explanation"]).strip()

        if question.question_format == "MCQ":
            if question.correct_answer not in (question.choices or {}):
                return Response({"error": "Correct answer must be one of the choice letters"},
                                status=status.HTTP_400_BAD_REQUEST)
        elif question.question_format == "TF":
            question.correct_answer = question.correct_answer.capitalize()
            if question.correct_answer not in ("True", "False"):
                return Response({"error": "Correct answer must be True or False"},
                                status=status.HTTP_400_BAD_REQUEST)

        question.save()
        mark_material_audio_stale(question.node.material, scope="questions")
        return Response({
            "id": question.id,
            "question_text": question.question_text,
            "question_format": question.question_format,
            "choices": question.choices,
            "correct_answer": question.correct_answer,
            "explanation": question.explanation,
            "thinking_order": question.thinking_order,
            "difficulty": question.difficulty,
            "bloom_level": question.bloom_level,
            "category": question.category,
        })

    def delete(self, request, question_id):
        question = GeneratedQuestion.objects.select_related("node__material").filter(id=question_id).first()
        if question is None:
            return Response({"error": "Question not found"},
                            status=status.HTTP_404_NOT_FOUND)
        material = question.node.material
        deleted, _ = GeneratedQuestion.objects.filter(id=question_id).delete()
        if not deleted:
            return Response({"error": "Question not found"},
                            status=status.HTTP_404_NOT_FOUND)
        mark_material_audio_stale(material, scope="questions")
        return Response(status=status.HTTP_204_NO_CONTENT)


class GenerationRunsView(APIView):
    """GET /api/generation/runs/?material_id=<id> — recent runs, newest first."""
    def get(self, request):
        runs = GenerationRun.objects.select_related("material", "node").order_by("-started_at")
        material_id = request.query_params.get("material_id")
        if material_id:
            runs = runs.filter(material_id=material_id)
        return Response([
            {
                "id": r.id,
                "material_id": r.material_id,
                "material_title": r.material.title,
                "node_id": r.node_id,
                "node_title": r.node.title if r.node else None,
                "status": r.status,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
            }
            for r in runs[:20]
        ])


class GenerationTraceView(APIView):
    """
    GET /api/generation/runs/<run_id>/events/?after=<seq>

    Returns run status plus events with seq > after (default: all).
    Poll this with the last seq already received.
    """
    def get(self, request, run_id):
        try:
            run = GenerationRun.objects.select_related("material", "node").get(id=run_id)
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
                "material_id": run.material_id,
                "material_title": run.material.title,
                "node_id": run.node_id,
                "node_title": run.node.title if run.node else None,
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
