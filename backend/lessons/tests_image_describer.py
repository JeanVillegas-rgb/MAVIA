from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase, override_settings

from lessons.services import image_describer
from lessons.services.content_generator import describe_pdf_images

PNG = b"\x89PNG\r\n\x1a\n fake image bytes"


def _ok_response(payload):
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status = MagicMock()
    r.json.return_value = payload
    return r


class BuildPromptTests(TestCase):
    def test_prompt_asks_for_the_concept_not_the_layout(self):
        prompt = image_describer.build_prompt(
            lesson_title="Photosynthesis",
            caption="Figure 2. The light-dependent reactions",
            visible_text="chloroplast  thylakoid",
            nearby_text="Plants capture light energy in the chloroplast.",
        )
        self.assertIn("blind student", prompt)
        self.assertIn("TEACHES", prompt)
        self.assertIn("Do NOT", prompt)
        self.assertIn("colours", prompt)
        self.assertIn("Photosynthesis", prompt)
        self.assertIn("light-dependent reactions", prompt)


class DescribeImageTests(TestCase):
    def setUp(self):
        image_describer.reset_reachability_cache()

    def tearDown(self):
        image_describer.reset_reachability_cache()

    @patch("lessons.services.image_describer.requests.post")
    @patch("lessons.services.image_describer.requests.get")
    def test_returns_the_models_explanation(self, mock_get, mock_post):
        mock_get.return_value = _ok_response({})  # /api/tags reachable
        mock_post.return_value = _ok_response(
            {"response": "  Water travels from roots to leaves through xylem, pulled "
             "upward as water evaporates from the leaf surface.  "}
        )
        out = image_describer.describe_image_for_lesson(
            PNG, lesson_title="Transport in plants", caption="Fig 3"
        )
        self.assertEqual(
            out,
            "Water travels from roots to leaves through xylem, pulled upward as "
            "water evaporates from the leaf surface.",
        )
        # image was sent base64-encoded, not as raw bytes
        sent = mock_post.call_args.kwargs["json"]
        self.assertEqual(len(sent["images"]), 1)
        self.assertNotIn(PNG, sent["images"])

    @patch("lessons.services.image_describer.requests.post")
    @patch("lessons.services.image_describer.requests.get")
    def test_skip_sentinel_becomes_empty(self, mock_get, mock_post):
        mock_get.return_value = _ok_response({})
        mock_post.return_value = _ok_response({"response": "SKIP"})
        self.assertEqual(image_describer.describe_image_for_lesson(PNG), "")

    @patch("lessons.services.image_describer.requests.post")
    @patch("lessons.services.image_describer.requests.get")
    def test_ollama_down_returns_empty_and_skips_generate(self, mock_get, mock_post):
        mock_get.side_effect = requests.ConnectionError("refused")
        self.assertEqual(image_describer.describe_image_for_lesson(PNG), "")
        mock_post.assert_not_called()

    @patch("lessons.services.image_describer.requests.post")
    @patch("lessons.services.image_describer.requests.get")
    def test_timeout_during_generate_returns_empty(self, mock_get, mock_post):
        mock_get.return_value = _ok_response({})
        mock_post.side_effect = requests.Timeout("too slow")
        self.assertEqual(image_describer.describe_image_for_lesson(PNG), "")

    @patch("lessons.services.image_describer.requests.get")
    def test_no_image_bytes_never_touches_the_network(self, mock_get):
        self.assertEqual(image_describer.describe_image_for_lesson(b""), "")
        mock_get.assert_not_called()

    @override_settings(IMAGE_DESCRIPTION_ENABLED=False)
    @patch("lessons.services.image_describer.requests.get")
    def test_feature_flag_off(self, mock_get):
        self.assertEqual(image_describer.describe_image_for_lesson(PNG), "")
        mock_get.assert_not_called()


class DescribePdfImagesIntegrationTests(TestCase):
    def setUp(self):
        image_describer.reset_reachability_cache()

    def tearDown(self):
        image_describer.reset_reachability_cache()

    def _image(self):
        return {
            "page_number": 1, "index": 0, "width": 100, "height": 80,
            "extension": "png", "image_bytes": PNG,
            "caption": "Figure 1. The water cycle",
            "visible_text": "evaporation condensation",
        }

    @patch("lessons.services.image_describer.requests.post")
    @patch("lessons.services.image_describer.requests.get")
    def test_model_description_feeds_the_learning_object(self, mock_get, mock_post):
        mock_get.return_value = _ok_response({})
        mock_post.return_value = _ok_response(
            {"response": "Water evaporates from oceans, forms clouds, and returns "
             "as rain, cycling endlessly between the sea, sky, and land."}
        )
        [described] = describe_pdf_images([self._image()], "The water cycle", "")
        self.assertIn("evaporates from oceans", described["description"])
        self.assertEqual(described["description"], described["content"])
        self.assertEqual(described["educational_purpose"], described["description"])
        self.assertEqual(described["description_source"], "vision_model")

    def test_use_model_false_falls_back_to_caption(self):
        [described] = describe_pdf_images([self._image()], "The water cycle", "", use_model=False)
        self.assertEqual(described["description"], "Figure 1. The water cycle")
        self.assertEqual(described["educational_purpose"], "")
        self.assertEqual(described["description_source"], "caption_or_visible_text")

    @patch("lessons.services.image_describer.requests.get")
    def test_ollama_unavailable_falls_back_without_error(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("refused")
        [described] = describe_pdf_images([self._image()], "The water cycle", "")
        self.assertEqual(described["description"], "Figure 1. The water cycle")
        self.assertEqual(described["description_source"], "caption_or_visible_text")
