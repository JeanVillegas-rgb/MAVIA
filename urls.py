from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CourseGroupViewSet, LessonViewSet

router = DefaultRouter()
router.register("courses", CourseGroupViewSet, basename="course")
router.register("lessons", LessonViewSet, basename="lesson")

urlpatterns = [
    path("", include(router.urls)),
]
