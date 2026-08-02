from django.urls import path

from .views import FirstLessonView, LessonPackageView, SyncCourseView

urlpatterns = [
    path("sync/<int:course_id>/", SyncCourseView.as_view(), name="adaptive-course-sync"),
    path("first-lesson/", FirstLessonView.as_view(), name="adaptive-first-lesson"),
    path("lesson-package/<int:node_id>/", LessonPackageView.as_view(), name="lesson-package"),
]
