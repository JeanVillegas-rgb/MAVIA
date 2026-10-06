from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from lessons.models import CourseGroup
from user.models import User
from user.permissions import IsTeacherOrAdmin

from .models import Enrollment
from .serializers import EnrollmentSerializer, StudentBaselineSerializer, StudentBriefSerializer
from .services import course_progress_report



#WEB REVIEW SECTION

class StudentSearchView(ListAPIView):
    #find students to enroll
    permission_classes = [IsTeacherOrAdmin]
    serializer_class = StudentBriefSerializer

    def get_queryset(self):
        query = (self.request.query_params.get("q") or "").strip()
        students = User.objects.filter(role=User.Role.STUDENT, is_active=True)
        if settings.EMAIL_VERIFICATION_REQUIRED:
            students = students.filter(is_verified=True)
        if query:
            students = students.filter(
                Q(email__icontains=query)
                |Q(first_name__icontains=query)
                |Q(last_name__icontains=query)
            )
        return students.order_by("-date_joined","id")[:20]


class CourseEnrollmentView(APIView):
    permission_classes = [IsTeacherOrAdmin]

    def get(self, request, course_id): #current student roster
        course = get_object_or_404(CourseGroup, pk=course_id)
        rows = course.enrollments.select_related("student").all()
        return Response(EnrollmentSerializer(rows, many=True).data)

    def post(self, request, course_id): #enroll da student
        course = get_object_or_404(CourseGroup, pk=course_id)
        student = get_object_or_404(User, pk=request.data.get("student_id"), role = User.Role.STUDENT)
        enrollment, created = Enrollment.objects.get_or_create(student=student, course=course, defaults={"created_by": request.user},)
        return Response(EnrollmentSerializer(enrollment).data, 
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,)


class CourseEnrollmentDetailView(APIView):
    # if mag del og student na gi enroll
    permission_classes = [IsTeacherOrAdmin]
    def delete(self, request, course_id, pk):
        enrollment = get_object_or_404(Enrollment, pk=pk, course_id=course_id)
        enrollment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CourseProgressView(APIView):
    permission_classes = [IsTeacherOrAdmin]
    def get(self,request, course_id):
        course = get_object_or_404(CourseGroup, pk=course_id)
        report = course_progress_report(course)
        for row in report["rows"]:
            row["student"] = StudentBriefSerializer(row["student"]).data
            row["baseline"] =StudentBaselineSerializer(row["baseline"]).data if row["baseline"] else None
        return Response(report)