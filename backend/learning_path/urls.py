from django.urls import path

from . import views

urlpatterns = [
    path("topics/<int:node_id>/", views.topic_learning_path, name="topic-learning-path"),
    path("topics/<int:node_id>/published/", views.published_learning_path, name="published-learning-path"),
    path("topics/<int:node_id>/links/", views.add_path_link, name="add-path-link"),
    path("topics/<int:node_id>/links/move/", views.move_path_link, name="move-path-link"),
    path("topics/<int:node_id>/links/restore/", views.restore_path_links, name="restore-path-links"),
    path("topics/<int:node_id>/links/<int:link_id>/decision/", views.decide_path_link, name="decide-path-link"),
]
