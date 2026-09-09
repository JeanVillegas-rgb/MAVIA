from django.urls import path

from .views import (
    GenerationRunsView,
    GenerationTraceView,
    GetQuestionView,
    MaterialQuestionsView,
    NodeQuestionsView,
    QuestionDetailView,
    QuestionStatsView,
    StartGenerationView,
    SubmitAnswerView,
)

urlpatterns = [
    path("questions/", GetQuestionView.as_view()),
    path("questions/submit/", SubmitAnswerView.as_view()),
    path("questions/stats/", QuestionStatsView.as_view()),
    path("generation/questions/<int:question_id>/", QuestionDetailView.as_view()),
    path("generation/nodes/<int:node_id>/questions/", NodeQuestionsView.as_view()),
    path("generation/materials/<int:material_id>/start/", StartGenerationView.as_view()),
    path("generation/materials/<int:material_id>/nodes/<int:node_id>/start/", StartGenerationView.as_view()),
    path("generation/materials/<int:material_id>/questions/", MaterialQuestionsView.as_view()),
    path("generation/runs/", GenerationRunsView.as_view()),
    path("generation/runs/<int:run_id>/events/", GenerationTraceView.as_view()),
]
