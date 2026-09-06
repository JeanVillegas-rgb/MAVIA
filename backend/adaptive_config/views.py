from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from user.permissions import IsAdmin

from .models import AdaptiveConfig
from .serializers import AdaptiveConfigSerializer


class AdaptiveConfigView(generics.RetrieveUpdateAPIView):
    """GET current adaptive-engine weights; PATCH/PUT to change them.
    Admin-only — these numbers change how the scoring model behaves for
    every learner."""

    permission_classes = [IsAdmin]
    serializer_class = AdaptiveConfigSerializer

    def get_object(self):
        return AdaptiveConfig.load()

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


class AdaptiveConfigResetView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request):
        AdaptiveConfig.objects.filter(id=1).delete()
        config = AdaptiveConfig.load()
        config.updated_by = request.user
        config.save()
        return Response(AdaptiveConfigSerializer(config).data)
