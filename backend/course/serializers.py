from rest_framework import serializers

from .models import CourseModule, LessonNode
from .question_formatting import choice_texts
from .services import VARIANT_KEYS, _material_text_for_source


class LessonNodeSerializer(serializers.ModelSerializer):
    variants = serializers.SerializerMethodField()
    questions = serializers.SerializerMethodField()

    class Meta:
        model = LessonNode
        fields = ["id", "title", "variants", "questions"]

    def get_variants(self, obj):
        # Mirror LessonPackageService.build_package: always return all three
        # variant keys, falling back to the lesson's plain-text content (or
        # title) when a NORMAL/ELABORATED/SIMPLIFIED row hasn't been
        # generated yet, instead of silently omitting the key.
        saved_variants = {v.variant.lower(): v for v in obj.variants.all()}

        fallback_text = _material_text_for_source(obj.source)
        if not fallback_text:
            description = ""
            if obj.source.outline_node_id:
                description = obj.source.outline_node.related_info.get("description", "")
            fallback_text = description or obj.title

        return {
            key: {
                "text": saved_variants[key].narration if key in saved_variants else fallback_text,
                "audio_url": saved_variants[key].audio_url if key in saved_variants else "",
            }
            for key in VARIANT_KEYS
        }

    def get_questions(self, obj):
        result = {}
        module_questions = obj.module_questions.select_related("question").order_by("order", "id")
        for module_question in module_questions:
            question = module_question.question
            key = module_question.bloom_level.lower()
            result.setdefault(key, []).append(
                {
                    "id": question.id,
                    "question": question.question_text,
                    "choices": choice_texts(question),
                    # correct_answer intentionally omitted here: this
                    # serializer backs the student-facing "take the quiz"
                    # endpoint. LessonPackageService.build_package still
                    # includes it for server-side grading use.
                }
            )
        return result


class CourseModuleSerializer(serializers.ModelSerializer):
    lesson_nodes = LessonNodeSerializer(many=True, read_only=True)

    class Meta:
        model = CourseModule
        fields = ["id", "sequence_order", "title", "lesson_nodes"]
