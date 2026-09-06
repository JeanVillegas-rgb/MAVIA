from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CourseGroupViewSet

router = DefaultRouter()
router.register("courses", CourseGroupViewSet, basename="course")

urlpatterns = [
    path("", include(router.urls)),
]
