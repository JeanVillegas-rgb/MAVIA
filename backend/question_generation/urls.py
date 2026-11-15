from django.urls import path

from .views import (
    GenerationRunsView,
    GenerationTraceView,
    StartGenerationView,
    StartTopicGenerationView,
)

urlpatterns = [
    path("generation/materials/<int:material_id>/nodes/<int:node_id>/start/", StartGenerationView.as_view()),
    path("generation/topics/<int:outline_node_id>/start/", StartTopicGenerationView.as_view()),
    path("generation/runs/", GenerationRunsView.as_view()),
    path("generation/runs/<int:run_id>/events/", GenerationTraceView.as_view()),
]
