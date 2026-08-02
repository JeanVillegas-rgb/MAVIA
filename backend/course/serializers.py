from rest_framework import serializers

from .models import CourseModule, LessonNode, LessonVariant, ModuleQuestion


class LessonVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = LessonVariant
        fields = "__all__"


class ModuleQuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModuleQuestion
        fields = "__all__"


class LessonNodeSerializer(serializers.ModelSerializer):
    variants = LessonVariantSerializer(many=True, read_only=True)
    module_questions = ModuleQuestionSerializer(many=True, read_only=True)
    title = serializers.CharField(read_only=True)

    class Meta:
        model = LessonNode
        fields = "__all__"


class CourseModuleSerializer(serializers.ModelSerializer):
    lesson_nodes = LessonNodeSerializer(many=True, read_only=True)
    title = serializers.CharField(read_only=True)

    class Meta:
        model = CourseModule
        fields = "__all__"
