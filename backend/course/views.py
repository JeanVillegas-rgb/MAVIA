from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import LessonPackageService, first_lesson_node, sync_course_outline


class SyncCourseView(APIView):
    def post(self, request, course_id):
        modules = sync_course_outline(course_id)
        return Response(
            {
                "course_id": course_id,
                "modules": modules.count(),
                "lesson_nodes": sum(module.lesson_nodes.count() for module in modules),
            }
        )


class FirstLessonView(APIView):
    def get(self, request):
        course_id = request.query_params.get("course_id")
        node = first_lesson_node(int(course_id) if course_id else None)
        if node is None:
            return Response(
                {"error": "No approved course outline is available."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(LessonPackageService.build_package(node.id))


class LessonPackageView(APIView):
    def get(self, request, node_id):
        return Response(LessonPackageService.build_package(node_id))
