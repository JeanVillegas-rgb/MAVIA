from django.urls import path

from .views import (
    CourseEnrollmentDetailView,
    CourseEnrollmentView,
    CourseProgressView,
    LearningStateDetailView,
    LessonDetailView,
    MyCourseLessonsView,
    MyCoursesView,
    StartLearningView,
    StudentSearchView,
    SubmitResponseView,
)

urlpatterns = [
    # student-facing
    path("start/", StartLearningView.as_view(), name="adaptive-portal-start"),
    path("submit-response/", SubmitResponseView.as_view(), name="adaptive-portal-submit"),
    path(
        "learning-states/<int:pk>/",
        LearningStateDetailView.as_view(),
        name="adaptive-portal-learning-state",
    ),
    path("my-courses/", MyCoursesView.as_view(), name="adaptive-portal-my-courses"),
    path(
        "my-courses/<int:course_id>/lessons/",
        MyCourseLessonsView.as_view(),
        name="adaptive-portal-my-course-lessons",
    ),
    path(
        "lessons/<int:lesson_id>/",
        LessonDetailView.as_view(),
        name="adaptive-portal-lesson-detail",
    ),
    # teacher-facing (web review section)
    path("students/", StudentSearchView.as_view(), name="adaptive-portal-student-search"),
    path(
        "courses/<int:course_id>/progress/",
        CourseProgressView.as_view(),
        name="adaptive-portal-course-progress",
    ),
    path(
        "courses/<int:course_id>/enrollments/",
        CourseEnrollmentView.as_view(),
        name="adaptive-portal-course-enrollments",
    ),
    path(
        "courses/<int:course_id>/enrollments/<int:pk>/",
        CourseEnrollmentDetailView.as_view(),
        name="adaptive-portal-course-enrollment-detail",
    ),
]
