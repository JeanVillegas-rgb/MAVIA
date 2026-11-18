from django.test import SimpleTestCase

from .generate_variation import VariationWriterError, parse_result


class VariationResponseTests(SimpleTestCase):
    def test_valid_json_returns_clean_content_and_reason(self):
        result = parse_result(
            '{"content":"Matter  takes up space.","reason":" Clearer wording. "}',
            "Matter has mass.",
        )

        self.assertEqual(result["content"], "Matter takes up space.")
        self.assertEqual(result["reason"], "Clearer wording.")

    def test_invalid_json_is_rejected(self):
        with self.assertRaises(VariationWriterError):
            parse_result("not json", "Matter has mass.")

    def test_unchanged_standard_is_rejected(self):
        with self.assertRaises(VariationWriterError):
            parse_result(
                '{"content":"Matter has mass.","reason":"Simpler."}',
                "Matter has mass.",
            )
