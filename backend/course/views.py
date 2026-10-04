from django.core.exceptions import ObjectDoesNotExist
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user.permissions import IsTeacherOrAdmin

from .services import (
    LessonPackageService,
    first_lesson_node,
    list_modules_with_lessons,
    sync_course_outline,
)


class SyncCourseView(APIView):
    permission_classes = [IsTeacherOrAdmin]

    def post(self, request, course_id):
        try:
            modules = sync_course_outline(course_id)
        except ObjectDoesNotExist:
            return Response(
                {"error": "No course found with that id."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {
                "course_id": course_id,
                "modules": modules.count(),
                "lesson_nodes": sum(module.lesson_nodes.count() for module in modules),
            }
        )


class FirstLessonView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        raw_course_id = request.query_params.get("course_id")
        course_id = None
        if raw_course_id:
            try:
                course_id = int(raw_course_id)
            except ValueError:
                return Response(
                    {"error": "course_id must be an integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        node = first_lesson_node(course_id)
        if node is None:
            return Response(
                {"error": "No approved course outline is available."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(LessonPackageService.build_package(node.id))


class ModuleListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        raw_course_id = request.query_params.get("course_id")
        course_id = None
        if raw_course_id:
            try:
                course_id = int(raw_course_id)
            except ValueError:
                return Response(
                    {"error": "course_id must be an integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            modules = list_modules_with_lessons(course_id)
        except ObjectDoesNotExist:
            return Response(
                {"error": "No course found with that id."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({"modules": modules})


class LessonPackageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, node_id):
        try:
            package = LessonPackageService.build_package(node_id)
        except ObjectDoesNotExist:
            return Response(
                {"error": "No lesson node found with that id."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(package)
