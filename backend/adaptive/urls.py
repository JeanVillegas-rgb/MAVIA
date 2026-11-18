from django.urls import path

from .views import (
    CourseEnrollmentDetailView,
    CourseEnrollmentView,
    CourseProgressView,
    StudentSearchView,
)

urlpatterns = [
    # teacher-facing (web review section)
    path("students/", StudentSearchView.as_view(), name="adaptive-student-search"),
    path(
        "courses/<int:course_id>/progress/",
        CourseProgressView.as_view(),
        name="adaptive-course-progress",
    ),
    path(
        "courses/<int:course_id>/enrollments/",
        CourseEnrollmentView.as_view(),
        name="adaptive-course-enrollments",
    ),
    path(
        "courses/<int:course_id>/enrollments/<int:pk>/",
        CourseEnrollmentDetailView.as_view(),
        name="adaptive-course-enrollment-detail",
    ),
]
