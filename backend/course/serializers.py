from rest_framework import serializers

from .models import CourseModule, LessonNode
from .question_formatting import choice_texts
from .services import VARIANT_KEYS, _material_text_for_source


class LessonNodeSerializer(serializers.ModelSerializer):
    variants = serializers.SerializerMethodField()
    questions = serializers.SerializerMethodField()

    class Meta:
        model = LessonNode
        fields = "__all__"


class CourseModuleSerializer(serializers.ModelSerializer):
    lesson_nodes = LessonNodeSerializer(many=True, read_only=True)

    class Meta:
        model = CourseModule
        fields = ["id", "sequence_order", "title", "lesson_nodes"]
