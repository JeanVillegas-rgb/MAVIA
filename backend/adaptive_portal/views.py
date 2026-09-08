from django.conf import settings
from django.db import transaction
from adaptive_config.models import AdaptiveConfig
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from lessons.models import CourseGroup, OutlineNode, Question
from lessons.services.lesson_package import (
    build_course_module_summaries,
    build_lesson_payload,
    build_module_package,
)
from user.models import User
from user.permissions import IsStudent, IsTeacherOrAdmin

from .models import Enrollment, LearningState, StudentResponse
from .serializers import (
    EnrollmentSerializer,
    LearningStateSerializer,
    StartLearningSerializer,
    StudentBriefSerializer,
    SubmitResponseSerializer,
)
from .services import AdaptiveEngine, ordered_course_steps, resolve_start, top_level_ancestor


# ---------------------------------------------------------------------------
# student-facing (adopted by the mobile app)
# ---------------------------------------------------------------------------

class StartLearningView(APIView):
    permission_classes = [IsStudent]

    def post(self, request):
        serializer = StartLearningSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course = get_object_or_404(CourseGroup, pk=serializer.validated_data["course_id"])

        if not Enrollment.objects.filter(student=request.user, course=course).exists():
            return Response(
                {"detail": "You are not enrolled in this course."},
                status=status.HTTP_403_FORBIDDEN,
            )

        state, created = LearningState.objects.get_or_create(
            student=request.user,
            course=course,
            defaults={"mastery": AdaptiveConfig.load().starting_mastery},
        )
        if created or state.current_question_id is None and not state.completed:
            module, node, question = resolve_start(course)
            state.current_module = module
            state.current_lesson_node = node
            state.current_question = question
            state.save()

        lesson = (
            build_lesson_payload(state.current_lesson_node)
            if state.current_lesson_node_id and state.current_lesson_node.published
            else None
        )
        return Response(
            {
                "learning_state": LearningStateSerializer(state).data,
                "lesson": lesson,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class SubmitResponseView(APIView):
    permission_classes = [IsStudent]

    @transaction.atomic
    def post(self, request):
        serializer = SubmitResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        state = get_object_or_404(
            LearningState.objects.select_for_update(), pk=data["learning_state_id"], student=request.user
        )
        if not Enrollment.objects.filter(student=request.user, course=state.course).exists():
            return Response({"detail": "Not enrolled."}, status=403)
        if state.completed or state.current_question_id != data["question_id"]:
            return Response({"detail": "Answer the currently assigned question."}, status=400)
        if not state.current_lesson_node or not state.current_lesson_node.published:
            return Response({"detail": "This lesson is no longer published."}, status=400)
        question = get_object_or_404(Question, pk=data["question_id"])

        result = AdaptiveEngine.evaluate(state, question, data["selected_answer"])
        StudentResponse.objects.create(
            learning_state=state,
            question=question,
            selected_answer=data["selected_answer"],
            is_correct=result["is_correct"],
        )

        next_lesson = (
            build_lesson_payload(state.current_lesson_node)
            if state.current_lesson_node_id
            else None
        )
        return Response({**result, "lesson": next_lesson})


class LearningStateDetailView(APIView):
    permission_classes = [IsStudent]

    def get(self, request, pk):
        state = get_object_or_404(LearningState, pk=pk, student=request.user)
        return Response(LearningStateSerializer(state).data)


class MyCoursesView(APIView):
    """Courses the signed-in student is enrolled in, with a light progress
    summary. Backs the mobile course list."""

    permission_classes = [IsStudent]

    def get(self, request):
        states = {
            s.course_id: s
            for s in LearningState.objects.filter(student=request.user)
        }
        out = []
        for enrollment in request.user.enrollments.select_related("course"):
            course = enrollment.course
            summaries = build_course_module_summaries(course, published_only=True)
            state = states.get(course.id)
            out.append(
                {
                    "id": course.id,
                    "title": course.title,
                    "description": course.description,
                    "module_count": len(summaries),
                    "lesson_count": sum(m["lesson_count"] for m in summaries),
                    "track_count": sum(m["track_count"] for m in summaries),
                    "question_count": sum(m["question_count"] for m in summaries),
                    "mastery": round(state.mastery, 3) if state else None,
                    "started": state is not None,
                    "completed": state.completed if state else False,
                }
            )
        return Response(out)


class MyCourseLessonsView(APIView):
    permission_classes = [IsStudent]

    def get(self, request, course_id):
        course = get_object_or_404(CourseGroup, pk=course_id)
        if not Enrollment.objects.filter(student=request.user, course=course).exists():
            return Response({"detail": "Not enrolled."}, status=status.HTTP_403_FORBIDDEN)
        lessons = []
        for module_node in course.nodes.filter(parent__isnull=True).order_by("order", "id"):
            package = build_module_package(module_node, published_only=True)
            for lesson in package["lessons"]:
                lessons.append({**lesson, "module_title": module_node.title})
        return Response(lessons)


class LessonDetailView(APIView):
    permission_classes = [IsStudent]

    def get(self, request, lesson_id):
        node = get_object_or_404(OutlineNode, pk=lesson_id, published=True)
        if not Enrollment.objects.filter(
            student=request.user, course=node.course
        ).exists():
            return Response({"detail": "Not enrolled."}, status=status.HTTP_403_FORBIDDEN)
        return Response(build_lesson_payload(node))


# ---------------------------------------------------------------------------
# teacher-facing (web review section)
# ---------------------------------------------------------------------------

class StudentSearchView(ListAPIView):
    permission_classes = [IsTeacherOrAdmin]
    serializer_class = StudentBriefSerializer

    def get_queryset(self):
        query = (self.request.query_params.get("q") or "").strip()
        students = User.objects.filter(role=User.Role.STUDENT, is_active=True)
        if settings.EMAIL_VERIFICATION_REQUIRED:
            students = students.filter(is_verified=True)
        if query:
            students = students.filter(
                Q(username__icontains=query)
                | Q(email__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
            )
        return students.order_by("-date_joined", "id")[:20]


class CourseEnrollmentView(APIView):
    permission_classes = [IsTeacherOrAdmin]

    def get(self, request, course_id):
        course = get_object_or_404(CourseGroup, pk=course_id)
        rows = course.enrollments.select_related("student").all()
        return Response(EnrollmentSerializer(rows, many=True).data)

    def post(self, request, course_id):
        course = get_object_or_404(CourseGroup, pk=course_id)
        student = get_object_or_404(
            User, pk=request.data.get("student_id"), role=User.Role.STUDENT
        )
        enrollment, created = Enrollment.objects.get_or_create(
            student=student,
            course=course,
            defaults={"created_by": request.user},
        )
        return Response(
            EnrollmentSerializer(enrollment).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class CourseEnrollmentDetailView(APIView):
    permission_classes = [IsTeacherOrAdmin]

    def delete(self, request, course_id, pk):
        enrollment = get_object_or_404(Enrollment, pk=pk, course_id=course_id)
        enrollment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CourseProgressView(APIView):
    permission_classes = [IsTeacherOrAdmin]

    def get(self, request, course_id):
        course = get_object_or_404(CourseGroup, pk=course_id)
        steps = ordered_course_steps(course)  # [(module, node, [questions])]
        total_modules = course.nodes.filter(parent__isnull=True).count()

        # module id -> set of question ids that must be answered correctly
        module_question_ids = {}
        for module, _node, questions in steps:
            module_question_ids.setdefault(module.id, set()).update(q.id for q in questions)

        states = {
            s.student_id: s
            for s in LearningState.objects.filter(course=course).select_related("student")
        }

        rows = []
        for enrollment in course.enrollments.select_related("student"):
            student = enrollment.student
            state = states.get(student.id)
            row = {
                "enrollment_id": enrollment.id,
                "student": StudentBriefSerializer(student).data,
                "started": state is not None,
                "mastery": round(state.mastery, 3) if state else None,
                "attempts": state.attempts if state else 0,
                "completed": state.completed if state else False,
                "last_activity": state.updated_at if state else None,
                "questions_answered": 0,
                "correct_rate": None,
                "modules_completed": 0,
                "total_modules": total_modules,
            }
            if state:
                responses = list(state.responses.all())
                row["questions_answered"] = len(responses)
                if responses:
                    row["correct_rate"] = round(
                        sum(1 for r in responses if r.is_correct) / len(responses), 3
                    )
                correct_ids = {r.question_id for r in responses if r.is_correct}
                row["modules_completed"] = sum(
                    1
                    for _mid, needed in module_question_ids.items()
                    if needed and needed.issubset(correct_ids)
                )
                if state.completed:
                    row["modules_completed"] = max(row["modules_completed"], total_modules)
            rows.append(row)

        return Response(
            {
                "course_id": course.id,
                "total_modules": total_modules,
                "content_ready": bool(steps),
                "rows": rows,
            }
        )
