"""A captioned figure is cropped to itself, and lettered captions are captions.

Measured on a Science 7 module (Q3): "Figure 2: Distance Traveled" sat in the
right column beside text, below a banner. The crop counted the banner and the
text beside it as part of the figure, grew to each text block it touched, and
became most of the page -- then removed the real figures it overlapped as
duplicates. And "Figure 5A: Speed" was not read as a caption at all, so it
became a lesson chunk's title.
"""

import os
import tempfile

import fitz
from django.test import SimpleTestCase

from lessons.services.content_generator import _image_caption_title, _is_image_caption
from lessons.services.instructional_content_classifier import find_captioned_figure_regions


def _png(width=120, height=80):
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
    pixmap.clear_with(200)
    return pixmap.tobytes("png")


class CaptionTests(SimpleTestCase):
    def test_lettered_and_colon_captions_are_captions(self):
        for caption, title in (
            ("Figure 5A: Speed", "Speed"),
            ("Figure 2: Distance Traveled", "Distance Traveled"),
            ("Fig. 3B. Waves", "Waves"),
            ("Figure 1. The main parts of a flower.", "The main parts of a flower"),
        ):
            with self.subTest(caption=caption):
                self.assertTrue(_is_image_caption(caption))
                self.assertEqual(_image_caption_title(caption), title)

    def test_a_paragraph_opening_with_a_figure_reference_is_not_a_caption(self):
        """Skipping it as a caption would drop lesson text."""
        text = ("Figure 2: the dog runs ten meters east, then five meters south and "
                "another ten meters west, so its total distance is twenty-five meters.")

        self.assertFalse(_is_image_caption(text))

    def test_accessible_diagram_description_is_a_caption(self):
        text = (
            "Diagram description: particles in a liquid are drawn as dots that "
            "are close together but scattered in an irregular, flowing "
            "arrangement rather than a neat grid, showing movement."
        )

        self.assertTrue(_is_image_caption(text))


class FigureCropTests(SimpleTestCase):
    def _two_column_page(self):
        document = fitz.open()
        page = document.new_page(width=595, height=842)
        # Page furniture: a banner drawn across the top.
        page.draw_rect(fitz.Rect(40, 40, 555, 80), color=(0, 0, 0), fill=(0, 0, 0))
        # Left column: lesson text, beside the figure.
        for row in range(8):
            page.insert_text((50, 120 + row * 22), "The dog runs ten meters to the east.", fontsize=10)
        # Right column: the figure and its caption below it.
        page.insert_image(fitz.Rect(330, 110, 550, 260), stream=_png())
        page.insert_text((360, 275), "Figure 5A: Speed", fontsize=10)
        handle, path = tempfile.mkstemp(suffix=".pdf")
        os.close(handle)
        document.save(path)
        document.close()
        self.addCleanup(os.remove, path)
        return fitz.open(path)

    def test_the_crop_is_the_figure_not_the_page(self):
        document = self._two_column_page()
        self.addCleanup(document.close)

        regions = find_captioned_figure_regions(document[0])

        self.assertEqual(len(regions), 1)
        crop = fitz.Rect(regions[0]["bbox"])
        self.assertEqual(regions[0]["caption"], "Figure 5A: Speed")
        # Nothing from the left column, nothing from the banner.
        self.assertGreater(crop.x0, 300)
        self.assertGreater(crop.y0, 90)
        self.assertNotIn("dog", regions[0]["visible_text"])

    def test_two_aligned_rows_are_one_captioned_figure(self):
        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_text((65, 250), "Learning Objectives: explain the lesson.", fontsize=10)
        page.insert_text((205, 325), "Techniques for Separating Mixtures", fontsize=11)
        for index, title in enumerate(("PICKING", "SIEVING", "WINNOWING", "MAGNET")):
            left = 85 + index * 122
            page.insert_text((left, 345), title, fontsize=9)
            page.draw_rect(fitz.Rect(left, 350, left + 74, 417), color=(0, 0, 0))
        for index, title in enumerate(("DECANTATION", "FILTERING", "EVAPORATION", "SCOOPING")):
            left = 85 + index * 122
            page.insert_text((left, 457), title, fontsize=9)
            page.draw_rect(fitz.Rect(left, 463, left + 74, 530), color=(0, 0, 0))
        page.insert_text((130, 575), "Figure 1. Each technique uses a property of the mixture.", fontsize=9)
        page.insert_text((65, 603), "Why Mixtures Can Be Separated", fontsize=11)
        self.addCleanup(document.close)

        regions = find_captioned_figure_regions(page)

        self.assertEqual(len(regions), 1)
        crop = fitz.Rect(regions[0]["bbox"])
        self.assertLess(crop.y0, 345)  # title and first row
        self.assertGreater(crop.y1, 575)  # second row and caption
        self.assertGreater(crop.y0, 250)  # not the learning objectives
        self.assertLess(crop.y1, 590)  # not the next section
        for title in ("PICKING", "SIEVING", "WINNOWING", "MAGNET",
                      "DECANTATION", "FILTERING", "EVAPORATION", "SCOOPING"):
            self.assertIn(title, regions[0]["visible_text"])
        self.assertNotIn("Learning Objectives", regions[0]["visible_text"])
        self.assertNotIn("Why Mixtures", regions[0]["visible_text"])

    def test_accessible_diagram_description_finds_vector_figure(self):
        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 80), "2. Solids", fontsize=14)
        page.insert_text(
            (72, 105),
            "Particles are packed tightly in a fixed pattern.",
            fontsize=10,
        )
        page.draw_rect(fitz.Rect(72, 130, 540, 270), color=(0, 0, 0))
        for row in range(4):
            for column in range(8):
                page.draw_circle(
                    fitz.Point(100 + column * 55, 155 + row * 30),
                    7,
                    color=(0, 0, 0),
                    fill=(0, 0, 0),
                )
        caption = (
            "Diagram description: particles in a solid are drawn as evenly spaced dots "
            "arranged in tidy rows and columns, like a grid, showing a fixed pattern."
        )
        page.insert_textbox(fitz.Rect(80, 278, 535, 315), caption, fontsize=8)
        self.addCleanup(document.close)

        regions = find_captioned_figure_regions(page)

        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["caption"].split(), caption.split())
        crop = fitz.Rect(regions[0]["bbox"])
        self.assertGreater(crop.y0, 110)
        self.assertGreaterEqual(crop.y1, 300)
        self.assertNotIn("Particles are packed tightly", regions[0]["visible_text"])
