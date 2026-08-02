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
from .services import AdaptiveScoringService


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

        node = first_lesson_node(course_id)
        if node is None:
            return Response(
                {"error": "No approved course outline is available."},
                status=status.HTTP_404_NOT_FOUND,
            )

        state = LearningState.objects.create(
            learner_id=learner_id,
            current_module=node.module,
            current_node=node,
        )

        return Response(
            {
                "learning_state_id": state.id,
                "current_node_id": node.id,
                "mastery": state.mastery,
                "current_variant": state.current_variant.lower(),
                "current_bloom": state.current_bloom.lower(),
                "lesson": LessonPackageService.build_package(node.id),
            },
            status=status.HTTP_201_CREATED,
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
