from django.urls import path

from . import views

urlpatterns = [
    path("my-courses/", views.MyCoursesView.as_view(), name="mobile-my-courses"),
    path("courses/<int:course_id>/", views.CourseContentView.as_view(), name="mobile-course-content"),
    path("topics/<int:topic_id>/", views.TopicPackageView.as_view(), name="mobile-topic-package"),
    path("topics/<int:topic_id>/continue/", views.ContinueView.as_view(), name="mobile-continue"),
    path("answers/", views.SubmitAnswerView.as_view(), name="mobile-submit-answer"),
]
