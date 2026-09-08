from django.urls import path

from .views import AdaptiveConfigResetView, AdaptiveConfigView

urlpatterns = [
    path("", AdaptiveConfigView.as_view(), name="adaptive-config"),
    path("reset/", AdaptiveConfigResetView.as_view(), name="adaptive-config-reset"),
]
