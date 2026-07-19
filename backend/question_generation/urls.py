from django.urls import path

from .views import (
    GenerationRunsView,
    GenerationTraceView,
    GetQuestionView,
    MaterialQuestionsView,
    QuestionStatsView,
    StartGenerationView,
    SubmitAnswerView,
)

urlpatterns = [
    path("questions/", GetQuestionView.as_view()),
    path("questions/submit/", SubmitAnswerView.as_view()),
    path("questions/stats/", QuestionStatsView.as_view()),
    path("generation/materials/<int:material_id>/start/", StartGenerationView.as_view()),
    path("generation/materials/<int:material_id>/questions/", MaterialQuestionsView.as_view()),
    path("generation/runs/", GenerationRunsView.as_view()),
    path("generation/runs/<int:run_id>/events/", GenerationTraceView.as_view()),
]
