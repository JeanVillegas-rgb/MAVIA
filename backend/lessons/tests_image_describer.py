from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from lessons.models import CourseGroup, LearningMaterial, LearningObject
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
        self.assertIn("2 to 4", prompt)
        self.assertIn("2 sentences for one simple idea", prompt)
        self.assertIn("3 to 4", prompt)
        self.assertIn("never write more than 6", prompt)
        self.assertIn("Do not add detail merely", prompt)

    def test_prompt_is_split_into_rtcf_sections(self):
        prompt = image_describer.build_prompt(
            lesson_title="Photosynthesis",
            caption="Figure 2. The light-dependent reactions",
            visible_text="chloroplast  thylakoid",
            nearby_text="Plants capture light energy in the chloroplast.",
        )

        for label in ("ROLE:", "TASK:", "CONTEXT", "FORMAT:"):
            self.assertIn(label, prompt)
        # The order the model reads them in.
        self.assertLess(prompt.index("ROLE:"), prompt.index("TASK:"))
        self.assertLess(prompt.index("TASK:"), prompt.index("CONTEXT"))
        self.assertLess(prompt.index("CONTEXT"), prompt.index("FORMAT:"))
        # The context is marked as background, not as material to reproduce.
        self.assertIn("background only", prompt)
        self.assertIn("Lesson title:", prompt)
        self.assertIn("Figure caption:", prompt)
        self.assertIn("Text printed inside the figure:", prompt)
        self.assertIn("Lesson text near the figure:", prompt)

    def test_an_empty_value_prints_no_stray_label(self):
        prompt = image_describer.build_prompt(lesson_title="Photosynthesis")

        self.assertIn("Lesson title:", prompt)
        self.assertNotIn("Figure caption:", prompt)
        self.assertNotIn("Text printed inside the figure:", prompt)
        self.assertNotIn("Lesson text near the figure:", prompt)

    def test_a_prompt_with_no_context_at_all_omits_the_section(self):
        prompt = image_describer.build_prompt()

        self.assertNotIn("CONTEXT", prompt)
        self.assertIn("ROLE:", prompt)
        self.assertIn("TASK:", prompt)
        self.assertIn("FORMAT:", prompt)

    def test_prompt_forbids_a_preamble(self):
        prompt = image_describer.build_prompt(lesson_title="States of Matter")

        self.assertIn("Write NO preamble", prompt)
        self.assertIn("no meta-sentence", prompt)
        self.assertIn("Start immediately with the content of the figure", prompt)

    def test_prompt_keeps_lesson_text_but_asks_for_what_the_figure_adds(self):
        prompt = image_describer.build_prompt(
            nearby_text="A solid keeps a fixed shape and a fixed volume.",
        )

        # The context stays -- the model needs it to know what is already said.
        self.assertIn("A solid keeps a fixed shape", prompt)
        self.assertIn("Describe only what the figure ADDS beyond it", prompt)
        self.assertIn("restate, summarise or paraphrase any of it back", prompt)

    def test_no_dont_restate_instruction_without_any_context(self):
        self.assertNotIn("ADDS beyond it", image_describer.build_prompt())

    def test_narration_is_capped_at_the_new_maximum(self):
        narration = " ".join(f"Sentence {number}." for number in range(1, 13))

        capped = image_describer._cap_narration_length(narration)

        self.assertEqual(
            len(image_describer._spoken_sentences(capped)),
            image_describer._MAX_NARRATION_SENTENCES,
        )
        self.assertEqual(image_describer._MAX_NARRATION_SENTENCES, 6)
        self.assertIn("Sentence 6.", capped)
        self.assertNotIn("Sentence 7.", capped)


class StripModelChatterTests(TestCase):
    """The safety net for a model that ignores the no-preamble instruction.

    Every one of these openers was measured on the live database, stored in
    the lesson and spoken aloud to the student.
    """

    def test_each_measured_opener_is_dropped(self):
        body = "Particles in a solid are packed tightly in a fixed pattern."
        for opener in (
            "Okay, let's describe this figure for the student.",
            "Okay, let us describe this figure for the student.",
            "Here's a description of the figure for your blind student:",
            "Here is a description of the figure for your blind student.",
            "Here is a spoken audio description of the figure.",
            "Sure, I can do that.",
            "Alright, I will describe this figure.",
        ):
            with self.subTest(opener=opener):
                self.assertEqual(
                    image_describer._strip_model_chatter(f"{opener} {body}"),
                    body,
                )

    def test_a_clean_description_is_left_untouched(self):
        for clean in (
            "Particles in a solid are packed tightly. They vibrate in place.",
            "Heating a solid gives its particles enough energy to break free.",
            "Here the three states are compared by shape and by volume.",
        ):
            with self.subTest(clean=clean):
                self.assertEqual(image_describer._strip_model_chatter(clean), clean)

    def test_a_lone_chatter_sentence_is_kept_rather_than_emptied(self):
        # Nothing else was written: dropping it would leave a blank narration,
        # and the caller treats "" as a failure to retry.
        text = "Okay, let's describe this figure."
        self.assertEqual(image_describer._strip_model_chatter(text), text)

    def test_a_colon_inside_the_lesson_text_is_never_cut_at(self):
        """The strip removes a preamble, never a clause of the description.

        Both of these begin with a chatter word and then teach. Cutting at
        the first colon in the whole text deleted everything before it --
        anywhere in the description -- and that mangled text is what a blind
        student hears.
        """
        for text in (
            "Okay, the particle diagram shows three states: solid, liquid "
            "and gas. They differ in spacing.",
            "The three states are compared by shape: a solid keeps its shape "
            "while a liquid flows. Gases fill the container.",
            "Matter takes three forms: solid, liquid and gas.",
        ):
            with self.subTest(text=text):
                self.assertEqual(image_describer._strip_model_chatter(text), text)

    def test_a_bare_interjection_sentence_goes_and_the_rest_survives_whole(self):
        lesson = (
            "The three states are compared by shape: a solid keeps its shape "
            "while a liquid flows. Gases fill the container."
        )

        self.assertEqual(
            image_describer._strip_model_chatter(f"Okay. {lesson}"), lesson,
        )

    def test_an_ambiguous_opener_word_is_not_treated_as_chatter(self):
        """"Right" and "Great" open lesson sentences as readily as chatter."""
        for text in (
            "Right after heating, the particles move faster. The solid melts.",
            "Great differences in spacing separate the three states. "
            "Solids are tightest.",
            "Sure footing depends on friction. The surface matters.",
        ):
            with self.subTest(text=text):
                self.assertEqual(image_describer._strip_model_chatter(text), text)

    def test_a_punctuated_interjection_is_still_chatter(self):
        body = "Particles in a solid are packed tightly."
        for opener in ("Sure, I will describe it.", "Certainly, I can do that."):
            with self.subTest(opener=opener):
                self.assertEqual(
                    image_describer._strip_model_chatter(f"{opener} {body}"), body,
                )

    def test_a_prefixed_refusal_is_still_a_skip(self):
        for refusal in ("SKIP", "Okay, SKIP.", "Sure. SKIP"):
            with self.subTest(refusal=refusal):
                stripped = image_describer._strip_model_chatter(refusal)
                self.assertTrue(image_describer._looks_like_skip(stripped))
        self.assertFalse(
            image_describer._looks_like_skip("Particles are packed tightly.")
        )


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
        self.assertEqual(sent["options"]["num_predict"], 512)

    @patch("lessons.services.image_describer.requests.post")
    @patch("lessons.services.image_describer.requests.get")
    def test_identical_request_reuses_cached_description(self, mock_get, mock_post):
        mock_get.return_value = _ok_response({})
        mock_post.return_value = _ok_response(
            {"response": "Particles in a solid remain close together and vibrate in place."}
        )

        first = image_describer.describe_image_for_lesson(
            PNG, lesson_title="States of matter", caption="Solid particles"
        )
        second = image_describer.describe_image_for_lesson(
            PNG, lesson_title="States of matter", caption="Solid particles"
        )

        self.assertEqual(second, first)
        mock_get.assert_called_once()
        mock_post.assert_called_once()

    @patch("lessons.services.image_describer.requests.post")
    @patch("lessons.services.image_describer.requests.get")
    def test_successful_reachability_check_is_reused_for_different_images(
        self, mock_get, mock_post
    ):
        mock_get.return_value = _ok_response({})
        mock_post.return_value = _ok_response({"response": "A useful explanation."})

        image_describer.describe_image_for_lesson(PNG, caption="First figure")
        image_describer.describe_image_for_lesson(PNG + b"2", caption="Second figure")

        mock_get.assert_called_once()
        self.assertEqual(mock_post.call_count, 2)

    @patch("lessons.services.image_describer.requests.post")
    @patch("lessons.services.image_describer.requests.get")
    def test_identical_request_reuses_cached_description(self, mock_get, mock_post):
        mock_get.return_value = _ok_response({})
        mock_post.return_value = _ok_response(
            {"response": "Particles in a solid remain close together and vibrate in place."}
        )

        first = image_describer.describe_image_for_lesson(
            PNG, lesson_title="States of matter", caption="Solid particles"
        )
        second = image_describer.describe_image_for_lesson(
            PNG, lesson_title="States of matter", caption="Solid particles"
        )

        self.assertEqual(second, first)
        mock_get.assert_called_once()
        mock_post.assert_called_once()

    @patch("lessons.services.image_describer.requests.post")
    @patch("lessons.services.image_describer.requests.get")
    def test_successful_reachability_check_is_reused_for_different_images(
        self, mock_get, mock_post
    ):
        mock_get.return_value = _ok_response({})
        mock_post.return_value = _ok_response({"response": "A useful explanation."})

        image_describer.describe_image_for_lesson(PNG, caption="First figure")
        image_describer.describe_image_for_lesson(PNG + b"2", caption="Second figure")

        mock_get.assert_called_once()
        self.assertEqual(mock_post.call_count, 2)

    @patch("lessons.services.image_describer.requests.post")
    @patch("lessons.services.image_describer.requests.get")
    def test_skip_sentinel_becomes_empty(self, mock_get, mock_post):
        mock_get.return_value = _ok_response({})
        mock_post.return_value = _ok_response({"response": "SKIP"})
        self.assertEqual(image_describer.describe_image_for_lesson(PNG), "")

    @patch("lessons.services.image_describer.requests.post")
    @patch("lessons.services.image_describer.requests.get")
    def test_a_prefixed_skip_sentinel_is_a_skip_not_a_description(self, mock_get, mock_post):
        # The chatter strip runs before the SKIP test, so a refusal the model
        # could not resist introducing is still a refusal -- not a narration
        # reading "Okay, SKIP." aloud to the student.
        mock_get.return_value = _ok_response({})
        mock_post.return_value = _ok_response({"response": "Okay, SKIP."})

        self.assertEqual(image_describer.describe_image_for_lesson(PNG), "")

    @patch("lessons.services.image_describer.requests.post")
    @patch("lessons.services.image_describer.requests.get")
    def test_ollama_down_returns_empty_and_skips_generate(self, mock_get, mock_post):
        mock_get.side_effect = requests.ConnectionError("refused")
        self.assertEqual(image_describer.describe_image_for_lesson(PNG), "")
        mock_post.assert_not_called()

    @patch("lessons.services.image_describer.requests.post")
    @patch("lessons.services.image_describer.requests.get")
    def test_ollama_can_recover_without_backend_restart(self, mock_get, mock_post):
        mock_get.side_effect = [requests.ConnectionError("refused"), _ok_response({})]
        mock_post.return_value = _ok_response({"response": "Particles represent the three states of matter."})

        self.assertEqual(image_describer.describe_image_for_lesson(PNG), "")
        self.assertEqual(
            image_describer.describe_image_for_lesson(PNG),
            "Particles represent the three states of matter.",
        )

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

    @patch("lessons.services.image_describer.describe_image_for_lesson")
    @patch("lessons.services.image_describer._learning_object_image_bytes", return_value=PNG)
    def test_missing_saved_narration_can_be_regenerated(self, _image_bytes, describe):
        describe.return_value = "Solid particles are packed closely, liquid particles can move, and gas particles are far apart."
        course = CourseGroup.objects.create(title="Science")
        material = LearningMaterial.objects.create(course=course, title="States of matter")
        learning_object = LearningObject.objects.create(
            material=material,
            kind=LearningObject.Kind.IMAGE,
            title="Particle arrangement",
            content="",
            image_url="/media/extracted_images/particles.png",
        )

        result = image_describer.populate_missing_image_descriptions(material)

        learning_object.refresh_from_db()
        self.assertEqual(result["generated_learning_object_ids"], [learning_object.id])
        self.assertIn("Solid particles", learning_object.content)


class RegenerateImageNarrationsApiTests(TestCase):
    @patch("lessons.views.populate_missing_image_descriptions")
    def test_repairs_confirmed_image_and_returns_generation_result(self, populate):
        course = CourseGroup.objects.create(title="Science")
        material = LearningMaterial.objects.create(
            course=course,
            title="States of matter",
            generated_json={"learning_objects_confirmed": True},
        )
        learning_object = LearningObject.objects.create(
            material=material,
            kind=LearningObject.Kind.IMAGE,
            title="Particle arrangement",
            content="",
            image_url="/media/extracted_images/particles.png",
        )
        generation_result = {
            "generated_count": 1,
            "generated_learning_object_ids": [learning_object.id],
            "errors": [],
        }

        def save_description(_material):
            learning_object.content = "Solid particles are packed in fixed positions."
            learning_object.save(update_fields=["content"])
            return generation_result

        populate.side_effect = save_description

        user = get_user_model().objects.create_user(
            username="image_narration_teacher",
            role=get_user_model().Role.TEACHER,
            is_verified=True,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            f"/api/courses/{course.id}/materials/{material.id}/regenerate-image-narrations/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["image_description_generation"], generation_result)
        returned_material = next(item for item in response.data["materials"] if item["id"] == material.id)
        self.assertEqual(
            returned_material["learning_objects"][0]["content"],
            "Solid particles are packed in fixed positions.",
        )
        self.assertIn(
            "Solid particles are packed in fixed positions.",
            returned_material["generated_json"]["narration_script"][0]["content"],
        )
        populate.assert_called_once_with(material)
