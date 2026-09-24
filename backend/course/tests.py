from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, TestCase, override_settings

from lessons.models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode

from .models import CourseModule, LessonNode, LessonVariant
from .variant_generator import (
    VariantGenerationError,
    _parse_response,
    _request_variants,
    generate_standalone_variants,
)


def _ollama_reply(text):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"response": text}
    return response


class VariantRequestRetryTests(SimpleTestCase):
    GOOD_REPLY = (
        '{"simplified": "A solid keeps its shape.",'
        ' "elaborated": "A solid keeps a fixed shape and a fixed volume."}'
    )

    def setUp(self):
        self.learning_object = SimpleNamespace(
            title="Solid",
            content="A solid has a fixed shape and a fixed volume.",
        )

    @patch("course.variant_generator.requests.post")
    def test_unreadable_reply_is_retried(self, post):
        post.side_effect = [_ollama_reply(""), _ollama_reply(self.GOOD_REPLY)]

        variants = _request_variants(self.learning_object, "gemma3:4b")

        self.assertEqual(variants["SIMPLIFIED"], "A solid keeps its shape.")
        self.assertEqual(post.call_count, 2)

    @patch("course.variant_generator.requests.post")
    def test_gives_up_after_three_unreadable_replies(self, post):
        post.side_effect = [_ollama_reply("not json") for _ in range(3)]

        with self.assertRaisesMessage(VariantGenerationError, "valid JSON"):
            _request_variants(self.learning_object, "gemma3:4b")
        self.assertEqual(post.call_count, 3)

    @patch("course.variant_generator.requests.post")
    def test_unreachable_ollama_is_not_retried(self, post):
        post.side_effect = requests.Timeout("timed out")

        with self.assertRaisesMessage(VariantGenerationError, "Gemma request failed"):
            _request_variants(self.learning_object, "gemma3:4b")
        self.assertEqual(post.call_count, 1)


@override_settings(
    ADAPTIVE_VARIANT_GENERATION_ENABLED=True,
    ADAPTIVE_VARIANT_LLM_MODEL="gemma3:4b",
)
class StandaloneVariantGenerationTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.module = OutlineNode.objects.create(
            course=self.course, title="Matter", depth=0, order=1
        )
        self.topic = OutlineNode.objects.create(
            course=self.course, parent=self.module, title="States", depth=1, order=1
        )
        self.material = LearningMaterial.objects.create(
            course=self.course,
            module_node=self.module,
            outline_node=self.topic,
            title="Lesson",
            generated_json={"learning_objects_confirmed": True},
            status=LearningMaterial.Status.COMPLETED,
        )
        course_module = CourseModule.objects.create(source=self.module)
        LessonNode.objects.create(module=course_module, source=self.material)

    def _object(self, title, content, group):
        return LearningObject.objects.create(
            material=self.material,
            group=group,
            title=title,
            content=content,
            kind=LearningObject.Kind.TEXT,
        )

    @patch("course.variant_generator._request_variants")
    def test_generates_only_for_confirmed_singleton_groups(self, request_variants):
        request_variants.return_value = {
            "SIMPLIFIED": "Matter takes up space.",
            "ELABORATED": "Matter has mass and also occupies an amount of space.",
        }
        singleton = LearningObjectGroup.objects.create(outline_node=self.topic, label="Matter")
        grouped = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solids")
        standalone = self._object("Matter", "Matter has mass and occupies space.", singleton)
        first = self._object("Solid", "A solid keeps its shape.", grouped)
        second = self._object("Solid", "Solid matter has a fixed shape.", grouped)

        result = generate_standalone_variants(self.topic)

        self.assertEqual(result["generated_count"], 2)
        self.assertEqual(request_variants.call_count, 1)
        self.assertEqual(set(standalone.variants.values_list("variant", flat=True)), {"SIMPLIFIED", "ELABORATED"})
        self.assertFalse(LessonVariant.objects.filter(learning_object__in=[first, second]).exists())

    @patch("course.variant_generator._request_variants")
    def test_unchanged_variants_are_cached_and_changed_content_is_regenerated(self, request_variants):
        request_variants.return_value = {
            "SIMPLIFIED": "Matter takes up space.",
            "ELABORATED": "Matter has mass and occupies physical space.",
        }
        group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Matter")
        learning_object = self._object("Matter", "Matter has mass and occupies space.", group)

        generate_standalone_variants(self.topic)
        cached = generate_standalone_variants(self.topic)
        self.assertEqual(cached["cached_count"], 2)
        self.assertEqual(request_variants.call_count, 1)

        learning_object.content = "Matter has mass, occupies space, and can change state."
        learning_object.save(update_fields=["content"])
        generate_standalone_variants(self.topic)
        self.assertEqual(request_variants.call_count, 2)

    @patch("course.variant_generator._request_variants")
    def test_generated_variants_are_removed_when_object_becomes_grouped(self, request_variants):
        request_variants.return_value = {
            "SIMPLIFIED": "Matter takes up space.",
            "ELABORATED": "Matter has mass and occupies physical space.",
        }
        group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Matter")
        original = self._object("Matter", "Matter has mass and occupies space.", group)
        generate_standalone_variants(self.topic)
        self.assertEqual(original.variants.count(), 2)

        self._object("Matter", "Anything with mass that occupies space is matter.", group)
        generate_standalone_variants(self.topic)

        self.assertFalse(original.variants.exists())
        self.assertEqual(request_variants.call_count, 1)

    def test_rejects_unreasonably_long_elaboration(self):
        raw = '{"simplified":"A solid has fixed shape and volume.","elaborated":"' + (
            "unsupported extra wording " * 20
        ) + '"}'
        with self.assertRaises(VariantGenerationError):
            _parse_response(raw, source_word_count=10)

    @patch("course.variant_generator._request_variants")
    def test_narrated_standalone_image_receives_adaptive_text_variants(self, request_variants):
        request_variants.return_value = {
            "SIMPLIFIED": "Solid particles are close together.",
            "ELABORATED": "The narration explains that particles in a solid are positioned close together.",
        }
        group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Particle figure")
        image = LearningObject.objects.create(
            material=self.material,
            group=group,
            title="Particle arrangement",
            content="Particles in a solid are packed close together.",
            kind=LearningObject.Kind.IMAGE,
            image_url="/media/extracted_images/particles.png",
        )

        result = generate_standalone_variants(self.topic)

        self.assertEqual(result["generated_count"], 2)
        self.assertEqual(image.variants.count(), 2)
