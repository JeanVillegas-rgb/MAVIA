from django.urls import path

from .views import (
    LearningStateDetail,
    LearningStateList,
    StartLearningView,
    SubmitResponseView,
)

urlpatterns = [
    path("start/", StartLearningView.as_view(), name="adaptive-start"),
    path("learning-states/", LearningStateList.as_view(), name="learning-state-list"),
    path("learning-states/<int:pk>/", LearningStateDetail.as_view(), name="learning-state-detail"),
    path("submit-response/", SubmitResponseView.as_view(), name="submit-response"),
]
