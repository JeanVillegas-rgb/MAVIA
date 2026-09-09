from django.urls import path

from . import views

urlpatterns = [
    path("materials/<int:material_id>/", views.material_learning_path, name="material-learning-path"),
    path("topics/<int:node_id>/", views.topic_learning_path, name="topic-learning-path"),
    path("edges/", views.create_edge, name="create-prerequisite-edge"),
    path("edges/<int:edge_id>/", views.delete_edge, name="delete-prerequisite-edge"),
]
