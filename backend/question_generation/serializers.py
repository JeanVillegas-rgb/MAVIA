from rest_framework import serializers

from .models import GeneratedQuestion


class QuestionSerializer(serializers.ModelSerializer):
    node_title = serializers.CharField(source="node.title", read_only=True)

    class Meta:
        model = GeneratedQuestion
        fields = [
            "id", "node", "node_title", "question_text",
            "question_format", "choices", "bloom_level",
            "thinking_order", "category",
        ]
        # NOTE: correct_answer intentionally excluded
        # frontend should NOT receive it until after submission
