"""A figure's printed caption is not its narration.

Measured on a DepEd module: with no model call at upload, Figure 3 was left
narrated as "Figure 3. Greenhouse Gas Effect: https://www.flickr.com/photos/
121935927@N06/13580531193" -- its label and a source link, read aloud to a
learner who cannot see the diagram. Publishing retried only blank narrations,
so it would never have been described.
"""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode
from lessons.services import image_describer
from lessons.services.content_generator import describe_pdf_images
from lessons.services.image_describer import (
    caption_without_links,
    is_caption_only,
    populate_missing_image_descriptions,
)

FLICKR_CAPTION = "Figure 3. Greenhouse Gas Effect: https://www.flickr.com/photos/121935927@N06/13580531193"
NARRATION = (
    "This diagram shows sunlight passing through the atmosphere to warm the Earth, "
    "while greenhouse gases hold some of that heat in."
)


class CaptionTests(SimpleTestCase):
    def test_a_source_link_is_removed_from_a_caption(self):
        self.assertEqual(caption_without_links(FLICKR_CAPTION), "Figure 3. Greenhouse Gas Effect")

    def test_a_printed_caption_is_not_a_narration(self):
        for caption in (FLICKR_CAPTION, "Figure 6. Typical sea-breeze", "Table 2: Properties of solids"):
            with self.subTest(caption=caption):
                self.assertTrue(is_caption_only(caption))

    def test_a_description_is_a_narration(self):
        self.assertFalse(is_caption_only(NARRATION))
        self.assertFalse(is_caption_only(""))


class UploadNarrationTests(SimpleTestCase):
    def _image(self, caption):
        return {
            "page_number": 18, "index": 0, "width": 400, "height": 300, "extension": "png",
            "caption": caption, "image_bytes": b"png", "bbox": (0, 0, 400, 300),
        }

    def test_the_model_is_given_the_caption_without_its_link(self):
        with patch("lessons.services.content_generator.describe_image_for_lesson", return_value=NARRATION) as describe:
            described = describe_pdf_images([self._image(FLICKR_CAPTION)], "Solar Energy")

        self.assertEqual(describe.call_args.kwargs["caption"], "Figure 3. Greenhouse Gas Effect")
        self.assertEqual(described[0]["content"], NARRATION)

    def test_without_a_model_the_caption_stands_in_without_its_link(self):
        described = describe_pdf_images([self._image(FLICKR_CAPTION)], "Solar Energy", use_model=False)

        self.assertEqual(described[0]["content"], "Figure 3. Greenhouse Gas Effect")


class PublishRetryTests(TestCase):
    def setUp(self):
        course = CourseGroup.objects.create(title="Science")
        topic = OutlineNode.objects.create(course=course, title="Atmosphere", order=0, depth=0)
        self.material = LearningMaterial.objects.create(course=course, outline_node=topic, title="Module")

    def _figure(self, content, title="Figure 3", caption=FLICKR_CAPTION, url="/media/figure.png"):
        # What extraction recorded for this image, as upload stores it.
        records = self.material.generated_json.setdefault("image_descriptions", [])
        records.append({"image_url": url, "caption": caption})
        self.material.save(update_fields=["generated_json"])
        return LearningObject.objects.create(
            material=self.material, kind=LearningObject.Kind.IMAGE, title=title,
            content=content, image_url=url,
        )

    def _populate(self):
        with patch.object(image_describer, "_learning_object_image_bytes", return_value=b"png"), \
                patch.object(image_describer, "describe_image_for_lesson", return_value=NARRATION) as describe:
            result = populate_missing_image_descriptions(self.material)
        return result, describe

    def test_a_caption_only_narration_is_described(self):
        figure = self._figure(FLICKR_CAPTION)

        result, describe = self._populate()

        figure.refresh_from_db()
        self.assertEqual(figure.content, NARRATION)
        self.assertEqual(result["generated_learning_object_ids"], [figure.id])
        # The model gets the printed caption, not just "Figure 3" -- and no link.
        self.assertEqual(describe.call_args.kwargs["caption"], "Figure 3. Greenhouse Gas Effect")

    def test_a_blank_narration_is_still_described(self):
        figure = self._figure("", title="Table on page 7", caption="", url="/media/table.png")

        self._populate()

        figure.refresh_from_db()
        self.assertEqual(figure.content, NARRATION)

    def test_a_teachers_short_narration_starting_like_a_caption_is_kept(self):
        """Judged against the recorded caption, not by looking like one."""
        written = "Figure 3: warm air rises over land and cool sea air replaces it."
        figure = self._figure(written)

        result, describe = self._populate()

        figure.refresh_from_db()
        self.assertEqual(figure.content, written)
        describe.assert_not_called()

    def test_a_real_narration_is_left_alone(self):
        written = "The diagram traces warm air rising from the land while cool air flows in from the sea."
        figure = self._figure(written)

        result, describe = self._populate()

        figure.refresh_from_db()
        self.assertEqual(figure.content, written)
        describe.assert_not_called()


class ManyFiguresTests(SimpleTestCase):
    """Every figure is narrated during upload unless a cap is configured."""

    def _image(self, index):
        return {
            "page_number": 5 + index, "index": index, "width": 300, "height": 200, "extension": "png",
            "caption": f"Figure {index + 1}. A labelled diagram of part {index + 1}.",
            "image_bytes": b"png", "bbox": (0, 0, 300, 200),
        }

    def test_upload_narrates_every_figure_by_default(self):
        from lessons.services.content_generator import figures_narrated_at_upload

        with patch.dict("os.environ", {}, clear=False) as environ:
            environ.pop("FIGURES_NARRATED_AT_UPLOAD", None)
            self.assertIsNone(figures_narrated_at_upload())
        with patch.dict("os.environ", {"FIGURES_NARRATED_AT_UPLOAD": "5"}):
            self.assertEqual(figures_narrated_at_upload(), 5)

    def test_a_configured_cap_narrates_only_the_first_figures(self):
        images = [self._image(index) for index in range(8)]

        with patch("lessons.services.content_generator.describe_image_for_lesson", return_value=NARRATION) as describe:
            described = describe_pdf_images(images, "Motion", model_limit=5)

        self.assertEqual(describe.call_count, 5)
        self.assertEqual([item["content"] for item in described[:5]], [NARRATION] * 5)
        # The rest keep their caption until publishing narrates them.
        self.assertEqual(described[7]["content"], "Figure 8. A labelled diagram of part 8.")

    def test_without_a_limit_every_figure_is_narrated(self):
        images = [self._image(index) for index in range(3)]

        with patch("lessons.services.content_generator.describe_image_for_lesson", return_value=NARRATION) as describe:
            describe_pdf_images(images, "Motion")

        self.assertEqual(describe.call_count, 3)


class FigureNarrationStatusTests(TestCase):
    def test_figures_still_on_their_caption_are_counted_as_pending(self):
        from lessons.services.image_describer import figure_narration_status

        course = CourseGroup.objects.create(title="Science")
        topic = OutlineNode.objects.create(course=course, title="Motion", order=0, depth=0)
        material = LearningMaterial.objects.create(
            course=course, outline_node=topic, title="Module",
            generated_json={"image_descriptions": [
                {"image_url": "/a.png", "caption": "Figure 1. Relative motion."},
                {"image_url": "/b.png", "caption": "Figure 2. Distance traveled."},
                {"image_url": "/c.png", "caption": ""},
            ]},
        )
        for url, content in (("/a.png", NARRATION), ("/b.png", "Figure 2. Distance traveled."), ("/c.png", "")):
            LearningObject.objects.create(
                material=material, kind=LearningObject.Kind.IMAGE, title="Figure",
                content=content, image_url=url,
            )

        self.assertEqual(figure_narration_status(material), {"figures": 3, "pending": 2})


class NarrationPendingTests(TestCase):
    def test_a_caption_stand_in_is_reported_pending_not_included(self):
        from lessons.serializers import LearningObjectSerializer

        course = CourseGroup.objects.create(title="Science")
        topic = OutlineNode.objects.create(course=course, title="Motion", order=0, depth=0)
        material = LearningMaterial.objects.create(
            course=course, outline_node=topic, title="Module",
            generated_json={"image_descriptions": [{"image_url": "/5a.png", "caption": "Figure 5A: Speed"}]},
        )
        stand_in = LearningObject.objects.create(
            material=material, kind=LearningObject.Kind.IMAGE, title="Figure 5A: Speed",
            content="Figure 5A: Speed", image_url="/5a.png",
        )
        narrated = LearningObject.objects.create(
            material=material, kind=LearningObject.Kind.IMAGE, title="Figure 1",
            content=NARRATION, image_url="/1.png",
        )
        text = LearningObject.objects.create(material=material, title="Speed", content="Speed is distance over time.")

        pending = {item.id: LearningObjectSerializer(item).data["narration_pending"] for item in (stand_in, narrated, text)}

        self.assertEqual(pending, {stand_in.id: True, narrated.id: False, text.id: False})
