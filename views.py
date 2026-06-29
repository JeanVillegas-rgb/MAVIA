from pathlib import Path

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from .models import AudioModule, CourseGroup, CourseOutline, Lesson, OutlineNode
from .serializers import (
    AudioModuleUpdateSerializer,
    CourseCreateSerializer,
    CourseDetailSerializer,
    CourseListSerializer,
    LessonDetailSerializer,
    LessonListSerializer,
    LessonUploadSerializer,
    ScriptUpdateSerializer,
)
from .services.outline_parser import build_dag_from_outline
from .services.pipeline import approve_script, publish_lesson, start_script_processing


class CourseGroupViewSet(viewsets.ModelViewSet):
    queryset = CourseGroup.objects.prefetch_related("nodes", "outline").all()
    parser_classes = [MultiPartParser, FormParser]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return CourseCreateSerializer
        if self.action == "retrieve":
            return CourseDetailSerializer
        return CourseListSerializer

    @action(detail=True, methods=["post"], url_path="upload-outline")
    def upload_outline(self, request, pk=None):
        course = self.get_object()
        outline_file = request.FILES.get("outline_file")
        if not outline_file:
            return Response(
                {"detail": "outline_file is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if hasattr(course, "outline"):
            course.outline.outline_file.delete(save=False)
            course.outline.delete()

        outline = CourseOutline.objects.create(course=course, outline_file=outline_file)
        extension = Path(outline_file.name).suffix or ".txt"
        build_dag_from_outline(course, outline.outline_file.path, extension)

        serializer = CourseDetailSerializer(course, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="nodes/(?P<node_id>[^/.]+)/upload-lesson")
    def upload_lesson(self, request, pk=None, node_id=None):
        course = self.get_object()
        try:
            node = course.nodes.get(id=node_id)
        except OutlineNode.DoesNotExist:
            return Response({"detail": "Outline node not found."}, status=status.HTTP_404_NOT_FOUND)

        if hasattr(node, "lesson"):
            return Response(
                {"detail": "This node already has a lesson. Delete it first to replace."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = LessonUploadSerializer(
            data=request.data,
            context={"outline_node": node, "course": course, "request": request},
        )
        serializer.is_valid(raise_exception=True)
        lesson = serializer.save()
        start_script_processing(lesson.id)

        output = LessonDetailSerializer(lesson, context={"request": request})
        return Response(output.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def published(self, request, pk=None):
        course = self.get_object()
        lessons = (
            Lesson.objects.filter(
                course=course,
                status=Lesson.Status.PUBLISHED,
            )
            .prefetch_related("audio_modules")
            .order_by("outline_node__depth", "outline_node__order")
        )
        serializer = LessonListSerializer(lessons, many=True, context={"request": request})
        return Response(serializer.data)


class LessonViewSet(viewsets.ModelViewSet):
    queryset = Lesson.objects.prefetch_related("pages__images", "audio_modules").all()
    parser_classes = [MultiPartParser, FormParser]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return LessonUploadSerializer
        if self.action == "retrieve":
            return LessonDetailSerializer
        return LessonListSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lesson = serializer.save()
        start_script_processing(lesson.id)
        output = LessonDetailSerializer(lesson, context={"request": request})
        return Response(output.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def reprocess(self, request, pk=None):
        lesson = self.get_object()
        if lesson.status in {Lesson.Status.PROCESSING, Lesson.Status.AUDIO_GENERATING}:
            return Response(
                {"detail": "Lesson is already being processed."},
                status=status.HTTP_409_CONFLICT,
            )
        start_script_processing(lesson.id)
        serializer = LessonDetailSerializer(lesson, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["patch"], url_path="update-script")
    def update_script(self, request, pk=None):
        lesson = self.get_object()
        if lesson.status != Lesson.Status.SCRIPT_REVIEW:
            return Response(
                {"detail": "Script can only be edited during script review."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = ScriptUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.update_lesson(lesson)
        output = LessonDetailSerializer(lesson, context={"request": request})
        return Response(output.data)

    @action(detail=True, methods=["post"], url_path="approve-script")
    def approve_script_action(self, request, pk=None):
        lesson = self.get_object()
        if lesson.status != Lesson.Status.SCRIPT_REVIEW:
            return Response(
                {"detail": "Script must be in review before approval."},
                status=status.HTTP_409_CONFLICT,
            )
        if not lesson.audio_modules.exists():
            return Response(
                {"detail": "No script modules found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        approve_script(lesson)
        output = LessonDetailSerializer(lesson, context={"request": request})
        return Response(output.data)

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        lesson = self.get_object()
        if lesson.status != Lesson.Status.AUDIO_REVIEW:
            return Response(
                {"detail": "Lesson must complete audio review before publishing."},
                status=status.HTTP_409_CONFLICT,
            )
        if not all(module.audio_file for module in lesson.audio_modules.all()):
            return Response(
                {"detail": "All audio modules must be generated before publishing."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        publish_lesson(lesson)
        output = LessonDetailSerializer(lesson, context={"request": request})
        return Response(output.data)

    @action(detail=True, methods=["patch"], url_path="modules/(?P<module_id>[^/.]+)")
    def update_module(self, request, pk=None, module_id=None):
        lesson = self.get_object()
        if lesson.status != Lesson.Status.SCRIPT_REVIEW:
            return Response(
                {"detail": "Modules can only be edited during script review."},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            module = lesson.audio_modules.get(id=module_id)
        except AudioModule.DoesNotExist:
            return Response({"detail": "Module not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = AudioModuleUpdateSerializer(module, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        output = LessonDetailSerializer(lesson, context={"request": request})
        return Response(output.data)
