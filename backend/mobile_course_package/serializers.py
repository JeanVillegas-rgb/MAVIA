from rest_framework import serializers

from .models import TopicPackageProgress

class SubmitAnswerSerializer(serializers.Serializer):
    topic_id = serializers.IntegerField()
    question_id = serializers.IntegerField()
    selected_answer = serializers.CharField(max_length=20)


class TopicPackageProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = TopicPackageProgress
        fields = ["current_step_position", "current_variant", "return_to_position", "completed", "updated_at"]


