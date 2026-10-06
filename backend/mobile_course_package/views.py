from django.shortcuts import render, get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from adaptive.models import Enrollment
from lessons.models import CourseGroup, OutlineNode
from user.permissions import IsStudent


from .models import TopicPackageProgress
from .serializers import SubmitAnswerSerializer, TopicPackageProgressSerializer
from .services import continue_after_listening, course_outline, get_package, next_question_on_open, submit_answer


def is_enrolled(student, course_id):
    return Enrollment.objects.filter(student=student, course_id=course_id).exists()


def not_enrolled():
    return Response({"detail": "Student is not enrolled in this course."}, status=status.HTTP_403_FORBIDDEN)

def not_ready():
    return Response({"detail":"This topic isn't ready yet."}, status=status.HTTP_404_NOT_FOUND)


# Create your views here.



class MyCoursesView(APIView):
    #GET the courses the student is enrolled in
    permission_classes = [IsStudent]


    def get(self,request):
        enrollments = Enrollment.objects.filter(student=request.user).select_related("course")
        return Response([
            {
                'id': e.course.id,
                'title': e.course.title,
                # for the home screen's progress bar: published topics, and how many this student finished
                'topic_count': OutlineNode.objects.filter(course=e.course, parent__isnull=False, published=True).count(),
                'completed_topic_count': TopicPackageProgress.objects.filter(
                    student=request.user, topic__course=e.course, completed=True
                ).count(),
            }
            for e in enrollments
        ])


class CourseContentView(APIView):
    #GET the course modules and its published lessons
    permission_classes = [IsStudent]

    def get(self, request, course_id):
        course = get_object_or_404(CourseGroup, pk=course_id)
        if not is_enrolled(request.user, course.id):
            return not_enrolled()
        return Response({'id': course.id, 'title': course.title, 'modules': course_outline(course)})

class TopicPackageView(APIView):
    #GET the audio package + the uh, asa si student so far
    permission_classes = [IsStudent]

    def get(self,request, topic_id):
        topic = get_object_or_404(OutlineNode, pk=topic_id, published=True)
        if not is_enrolled(request.user, topic.course_id):
            return not_enrolled()

        package = get_package(topic)
        if package is None:
            return not_ready()

        progress, _ = TopicPackageProgress.objects.get_or_create(student=request.user, topic=topic)
        return Response({ 
            'topic': {'id': topic_id, 'title': topic.title},
            'steps': package.steps,
            'progress': TopicPackageProgressSerializer(progress).data,
            # which question of the current step to ask after listening (null: listen only)
            'next_question_id': next_question_on_open(request.user, topic, package, progress),
        })



class SubmitAnswerView(APIView):
    #POST the student respo to be graded by the server before the engine decidez
    permission_classes = [IsStudent]

    def post(self, request):
        data = SubmitAnswerSerializer(data=request.data)
        data.is_valid(raise_exception=True)

        topic = get_object_or_404(OutlineNode, pk=data.validated_data["topic_id"], published = True)
        if not is_enrolled(request.user, topic.course_id):
            return not_enrolled()

        package = get_package(topic)
        if package is None:
            return not_ready()

        result = submit_answer(request.user, topic, package, data.validated_data["question_id"], data.validated_data['selected_answer'])
        if result is None:
            return Response({"detail": "That question isn't in this topic."}, status=status.HTTP_404_NOT_FOUND)
        return Response(result)


class ContinueView(APIView):
    #POST: after a listen-only step (no questions) has been heard, move on
    permission_classes = [IsStudent]

    def post(self, request, topic_id):
        topic = get_object_or_404(OutlineNode, pk=topic_id, published=True)
        if not is_enrolled(request.user, topic.course_id):
            return not_enrolled()

        package = get_package(topic)
        if package is None:
            return not_ready()

        result = continue_after_listening(request.user, topic, package)
        if result is None:
            return Response({"detail": "This step has questions to answer first."}, status=status.HTTP_409_CONFLICT)
        return Response({"next": result})