from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from course.services import LessonPackageService, first_lesson_node
from question_generation.models import GeneratedQuestion

from .models import LearningState, StudentResponse
from .serializers import (
    LearningStateSerializer,
    StartLearningSerializer,
    StudentResponseSerializer,
)
from .services import AdaptiveScoringService, resolve_start


class LearningStateList(generics.ListAPIView):
    queryset = LearningState.objects.all()
    serializer_class = LearningStateSerializer


class LearningStateDetail(generics.RetrieveAPIView):
    queryset = LearningState.objects.all()
    serializer_class = LearningStateSerializer


class StartLearningView(APIView):
    def post(self, request):
        serializer = StartLearningSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course_id = serializer.validated_data.get("course_id")
        learner_id = serializer.validated_data["learner_id"]

        try:
            node = first_lesson_node(course_id)
        except ObjectDoesNotExist:
            return Response(
                {"error": "No course found with that id."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if node is None:
            return Response(
                {"error": "No approved course outline is available."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Resume the learner's existing progress instead of spawning a new
        # LearningState (and resetting mastery/bloom/node) on every app open.
        state = (
            LearningState.objects.filter(learner_id=learner_id, completed=False)
            .order_by("-last_updated")
            .first()
        )
        created = state is None
        if created:
            _chunk, question = resolve_start(node)
            state = LearningState.objects.create(
                learner_id=learner_id,
                current_module=node.module,
                current_node=node,
                current_question=question,
                current_bloom=question.bloom_level if question else "remember",
            )
        elif state.current_question_id is None:
            # Resumed state with no resolved question yet (fresh row from
            # before this field existed, or a chunk whose pool ran dry).
            _chunk, question = resolve_start(state.current_node)
            if question is not None:
                state.current_question = question
                state.current_bloom = question.bloom_level
                state.save(update_fields=["current_question", "current_bloom"])

        return Response(
            {
                "learning_state_id": state.id,
                "current_node_id": state.current_node_id,
                "current_question": state.current_question_id,
                "mastery": state.mastery,
                "current_variant": state.current_variant.lower(),
                "current_bloom": state.current_bloom.lower(),
                "lesson": LessonPackageService.build_package(state.current_node_id),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class SubmitResponseView(APIView):
    def post(self, request):
        serializer = StudentResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        learning_state = get_object_or_404(LearningState, id=data["learning_state_id"])
        question = get_object_or_404(GeneratedQuestion, id=data["question_id"])

        result = AdaptiveScoringService.evaluate(
            learning_state,
            question,
            data["selected_answer"],
            data.get("response_time", 0),
        )

        StudentResponse.objects.create(
            learning_state=learning_state,
            question=question,
            selected_answer=data["selected_answer"],
            is_correct=result["is_correct"],
            response_time=data.get("response_time", 0),
            reward=result["reward"],
        )

        return Response(result)
