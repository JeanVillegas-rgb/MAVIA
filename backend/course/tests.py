from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .generate_variation import (
    VariantGenerationError,
    VariationWriterError,
    _request_variants,
)


class VariantRequestTests(SimpleTestCase):
    def setUp(self):
        self.learning_object = SimpleNamespace(
            title="Solid",
            content="A solid has a fixed shape and a fixed volume.",
        )

    @patch("course.generate_variation.generate_versions")
    def test_returns_both_generated_slots(self, generate_versions):
        generate_versions.return_value = {
            "SIMPLIFIED": "A solid keeps its shape.",
            "ELABORATED": "A solid keeps a fixed shape and volume.",
        }

        result = _request_variants(self.learning_object)

        self.assertEqual(result["SIMPLIFIED"], "A solid keeps its shape.")
        self.assertIn("ELABORATED", result)

    @patch("course.generate_variation.generate_versions")
    def test_translates_writer_errors_for_the_persistence_layer(self, generate_versions):
        generate_versions.side_effect = VariationWriterError("invalid JSON")

        with self.assertRaisesMessage(VariantGenerationError, "invalid JSON"):
            _request_variants(self.learning_object)
