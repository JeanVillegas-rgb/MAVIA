from django.urls import path

from .views import (
    GenerationRunsView,
    GenerationTraceView,
    KeepQuestionView,
    KeepQuestionsView,
    LabelQuestionsView,
    QuestionPairingView,
    QuestionnaireUploadView,
    StartGenerationView,
    StartTopicGenerationView,
    TeacherQuestionEditView,
    TeacherQuestionView,
)

urlpatterns = [
    path("generation/materials/<int:material_id>/nodes/<int:node_id>/start/", StartGenerationView.as_view()),
    path("generation/topics/<int:outline_node_id>/start/", StartTopicGenerationView.as_view()),
    path("generation/topics/<int:outline_node_id>/questions/", TeacherQuestionView.as_view()),
    path("generation/topics/<int:outline_node_id>/questions/label/", LabelQuestionsView.as_view()),
    path("generation/topics/<int:outline_node_id>/questions/<int:question_id>/", TeacherQuestionEditView.as_view()),
    path("generation/topics/<int:outline_node_id>/questions/<int:question_id>/pairing/", QuestionPairingView.as_view()),
    path("generation/topics/<int:outline_node_id>/questions/<int:question_id>/keep/", KeepQuestionView.as_view()),
    path("generation/topics/<int:outline_node_id>/questionnaire/", QuestionnaireUploadView.as_view()),
    path("generation/topics/<int:outline_node_id>/concepts/<int:group_id>/keep-questions/", KeepQuestionsView.as_view()),
    path("generation/runs/", GenerationRunsView.as_view()),
    path("generation/runs/<int:run_id>/events/", GenerationTraceView.as_view()),
]
