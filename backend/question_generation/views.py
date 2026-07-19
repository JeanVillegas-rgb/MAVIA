import threading

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from lessons.models import LearningMaterial

from .models import GeneratedQuestion, GenerationEvent, GenerationRun, LearnerResponse
from .serializers import QuestionSerializer


class GetQuestionView(APIView):
    """
    GET /api/questions/?node_id=<learning_object_id>&difficulty=easy&learner_id=learner_123

    Returns an unanswered question for this learner at the requested difficulty.
    """
    def get(self, request):
        node_id = request.query_params.get("node_id")
        difficulty = request.query_params.get("difficulty", "easy")
        learner_id = request.query_params.get("learner_id")

        if not node_id or not learner_id:
            return Response(
                {"error": "node_id and learner_id required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        answered_ids = LearnerResponse.objects.filter(
            learner_id=learner_id,
            question__node_id=node_id,
        ).values_list("question_id", flat=True)

        question = (
            GeneratedQuestion.objects
            .filter(node_id=node_id, difficulty=difficulty)
            .exclude(id__in=answered_ids)
            .order_by("?")
            .first()
        )

        if not question:
            return Response(
                {"message": "No more questions at this difficulty"},
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

    Per-node progress stats for a learner, broken down by difficulty.
    """
    def get(self, request):
        node_id = request.query_params.get("node_id")
        learner_id = request.query_params.get("learner_id")

        if not node_id or not learner_id:
            return Response(
                {"error": "node_id and learner_id required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        questions = GeneratedQuestion.objects.filter(node_id=node_id)
        responses = LearnerResponse.objects.filter(
            learner_id=learner_id,
            question__node_id=node_id,
        )

        by_difficulty = {}
        for diff in ("easy", "medium", "hard"):
            diff_responses = responses.filter(question__difficulty=diff)
            by_difficulty[diff] = {
                "total": questions.filter(difficulty=diff).count(),
                "answered": diff_responses.values("question_id").distinct().count(),
                "correct": diff_responses.filter(is_correct=True)
                    .values("question_id").distinct().count(),
            }

        return Response({
            "total": sum(d["total"] for d in by_difficulty.values()),
            "answered": sum(d["answered"] for d in by_difficulty.values()),
            "correct": sum(d["correct"] for d in by_difficulty.values()),
            "by_difficulty": by_difficulty,
        })


# ── Generation pipeline (teacher-facing) ──

# If a "running" run has emitted nothing for this long, assume the server
# restarted mid-run and the thread is gone.
STALE_RUN_SECONDS = 300


def _run_pipeline(run_id, material_id):
    """Thread target: run the full pipeline, streaming trace events to the DB."""
    from .services.pipeline import generate_questions_for_material, save_questions_to_db

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
        questions = generate_questions_for_material(material, on_event=on_event)
        created = save_questions_to_db(material, questions)
        on_event("saved", f"Saved {len(created)} questions to database",
                 {"count": len(created)})
        GenerationRun.objects.filter(id=run_id).update(
            status="finished", finished_at=timezone.now())
    except Exception as e:  # noqa: BLE001 — surface anything to the trace
        on_event("error", f"{type(e).__name__}: {e}", None)
        GenerationRun.objects.filter(id=run_id).update(
            status="failed", finished_at=timezone.now())


class StartGenerationView(APIView):
    """
    POST /api/generation/materials/<material_id>/start/

    Teacher-triggered: generate questions for every text learning object of
    a completed material. Runs in the background; poll the trace endpoint.
    """
    def post(self, request, material_id):
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

        if not material.learning_objects.filter(kind="text").exclude(content="").exists():
            return Response(
                {"error": "Material has no text learning objects to generate from"},
                status=status.HTTP_400_BAD_REQUEST,
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

        run = GenerationRun.objects.create(material=material)
        threading.Thread(
            target=_run_pipeline, args=(run.id, material.id), daemon=True,
        ).start()
        return Response({"run_id": run.id}, status=status.HTTP_201_CREATED)


class MaterialQuestionsView(APIView):
    """
    GET /api/generation/materials/<material_id>/questions/

    Teacher-facing review of the stored question bank, grouped per learning
    object. Includes correct answers — never expose this to learners.
    """
    DIFFICULTY_ORDER = {"easy": 0, "medium": 1, "hard": 2}

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
                node.generated_questions.all(),
                key=lambda q: (self.DIFFICULTY_ORDER.get(q.difficulty, 3), q.id),
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
                        "difficulty": q.difficulty,
                        "bloom_level": q.bloom_level,
                        "difficulty_match": q.difficulty_match,
                    }
                    for q in questions
                ],
            })
        return Response(payload)


class GenerationRunsView(APIView):
    """GET /api/generation/runs/?material_id=<id> — recent runs, newest first."""
    def get(self, request):
        runs = GenerationRun.objects.select_related("material").order_by("-started_at")
        material_id = request.query_params.get("material_id")
        if material_id:
            runs = runs.filter(material_id=material_id)
        return Response([
            {
                "id": r.id,
                "material_id": r.material_id,
                "material_title": r.material.title,
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
            run = GenerationRun.objects.select_related("material").get(id=run_id)
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
