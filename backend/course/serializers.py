from rest_framework import serializers

from question_generation.models import GeneratedQuestion
from .models import CourseModule, LessonNode, LessonVariant, ModuleQuestion


class GeneratedQuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeneratedQuestion
        fields = ["id", "question_text", "choices"]


class LessonNodeSerializer(serializers.ModelSerializer):
    variants = serializers.SerializerMethodField()
    questions = serializers.SerializerMethodField()

    class Meta:
        model = LessonNode
        fields = ["id", "title", "variants", "questions"]

    def get_variants(self, obj):
        return {
            v.variant.lower(): {
                "text": v.narration,
                "audio_url": v.audio_url,
            }
            for v in obj.variants.all()
        }

    def get_questions(self, obj):
        result = {}
        for mq in obj.module_questions.select_related("question").all():
            key = mq.bloom_level.lower()
            result.setdefault(key, []).append({
                "id": mq.question.id,
                "question": mq.question.question_text,
                "choices": mq.question.choices,
            })
        return result


class CourseModuleSerializer(serializers.ModelSerializer):
    lesson_nodes = LessonNodeSerializer(many=True, read_only=True)

    class Meta:
        model = CourseModule
        fields = ["id", "sequence_order", "title", "lesson_nodes"]
