"""A figure inherits the heading it sits under, like every other passage.

`section_title` is the primary grouping signal: consecutive objects sharing a
heading form a run, and a run is how the system discovers that several
passages are one teachable unit. Measured on the three published topics,
**every one of the nine figures had an empty `section_title`** -- 26 of 75
objects overall.

It is not a detection failure. In `build_section_learning_objects`, images are
turned into items in their own loop *before* the text walk begins, so at the
moment an image is built no heading context exists yet; the heading is only
known later, and the items are merged back together by document position at
the end. The heading was always knowable, just not at the moment the image
was made.

Downstream, `unit_matching.find_units` already carries a workaround: it
extends a run when the next object's *title* equals the heading, added because
"extraction sometimes drops the section of a figure but keeps its title".
These tests make that a convenience rather than the thing holding figures into
their sections.
"""

from django.test import SimpleTestCase

from .services.content_generator import (
    build_section_learning_objects,
    classify_instructional_blocks,
)


def _heading(block_id, page, text, y, size=16.0):
    """A numbered heading: only those open a section the children sit under."""
    return {
        "block_id": block_id, "page": page, "text": text, "line_count": 1,
        "is_bold": True, "font_size": size, "bbox": (60, y, 220, y + 18),
    }


def _sub(block_id, page, text, y, size=12.0):
    return {
        "block_id": block_id, "page": page, "text": text, "line_count": 1,
        "is_bold": True, "font_size": size, "bbox": (60, y, 200, y + 14),
    }


def _passage(block_id, page, text, y):
    return {
        "block_id": block_id, "page": page, "text": text,
        "line_count": 1, "font_size": 11.0, "bbox": (60, y, 460, y + 15),
    }


def _figure(page, y, title, index=0):
    return {
        "page_number": page, "index": index, "width": 400, "height": 150,
        "extension": "png", "image_url": f"/media/extracted_images/f{index}.png",
        "title": title, "visible_text": "", "bbox": (60, y, 460, y + 150),
        "is_table": False,
    }


def _solids_section(page=1):
    """One numbered section with two sub-headed passages, as PDF B is built."""
    return [
        _heading(1, page, "2. Solids", 40),
        _sub(2, page, "Shape", 80),
        _passage(3, page, "A solid keeps a fixed shape unless a force is applied to it.", 100),
        _sub(4, page, "Volume", 400),
        _passage(5, page, "A solid takes up the same amount of space in any container.", 420),
    ]


class FigureSectionTitleTests(SimpleTestCase):
    def _build(self, blocks, images):
        from unittest.mock import patch
        from .services import content_generator as module
        # The narration is not what these tests are about, and it would call
        # the vision model.
        with patch.object(
            module, "describe_pdf_images",
            lambda given, *a, **k: [
                {**image, "description": f"Narration for {image['title']}."} for image in given
            ],
        ):
            return build_section_learning_objects(
                classify_instructional_blocks(blocks), module.describe_pdf_images(images),
            )

    def _by_title(self, objects):
        return {item["title"]: item for item in objects}

    def test_a_figure_takes_the_heading_of_the_section_it_sits_in(self):
        objects = self._build(_solids_section(), [_figure(1, 200, "Particle grid")])

        self.assertEqual(self._by_title(objects)["Particle grid"]["section_title"], "Solids")

    def test_a_figure_does_not_borrow_a_heading_it_comes_before(self):
        """It belongs to the section above it, never the one further down."""
        blocks = _solids_section() + [
            _heading(6, 2, "3. Liquids", 40),
            _sub(7, 2, "Flow", 80),
            _passage(8, 2, "A liquid takes the shape of whatever container holds it.", 100),
        ]
        objects = self._build(blocks, [_figure(1, 200, "Particle grid")])

        self.assertEqual(self._by_title(objects)["Particle grid"]["section_title"], "Solids")

    def test_a_figure_after_a_later_section_takes_that_one(self):
        blocks = _solids_section() + [
            _heading(6, 2, "3. Liquids", 40),
            _sub(7, 2, "Flow", 80),
            _passage(8, 2, "A liquid takes the shape of whatever container holds it.", 100),
        ]
        objects = self._build(blocks, [_figure(2, 200, "Liquid particles")])

        self.assertEqual(self._by_title(objects)["Liquid particles"]["section_title"], "Liquids")

    def test_a_figure_before_any_heading_keeps_an_empty_section(self):
        """Inventing one would be worse than leaving it blank, as it is today."""
        objects = self._build(_solids_section(), [_figure(1, 10, "Cover picture")])

        self.assertEqual(self._by_title(objects)["Cover picture"]["section_title"], "")

    def test_the_section_carries_across_a_page_break(self):
        blocks = _solids_section() + [
            _passage(6, 2, "Solid particles vibrate without moving past each other.", 400),
        ]
        objects = self._build(blocks, [_figure(2, 100, "Grid on the next page")])

        self.assertEqual(
            self._by_title(objects)["Grid on the next page"]["section_title"], "Solids",
        )

    def test_a_text_passage_keeps_the_section_it_already_had(self):
        """The pass reads the text items; it must never overwrite them."""
        objects = self._build(_solids_section(), [_figure(1, 200, "Particle grid")])

        passages = [item for item in objects if item.get("type") != "image_description"]
        self.assertEqual(len(passages), 2)
        for item in passages:
            self.assertEqual(item["section_title"], "Solids")


class FigureRetitleTests(SimpleTestCase):
    """A figure titled from its own description must not keep the old one.

    A figure with no caption in the PDF takes its title from the first sentence
    of its narration, and that title is what names the concept and is read
    aloud. Rewriting the narration therefore leaves the title quoting text that
    no longer exists -- on the live data, a concept was still called "The image
    illustrates how the arrangement of tiny particles relates to the different
    states of matte" (cut mid-word at 100 characters) after its description had
    been replaced.
    """

    def setUp(self):
        from lessons.management.commands.regenerate_image_descriptions import retitle
        self.retitle = retitle

    class _Obj:
        def __init__(self, title):
            self.title = title

    def test_a_title_derived_from_the_old_description_is_re_derived(self):
        old = "The image illustrates how particles are arranged. It shows three states."
        new = "Particles are arranged differently in each state. Solids are packed tightly."

        self.assertEqual(
            self.retitle(self._Obj("The image illustrates how particles are arranged"), old, new),
            "Particles are arranged differently in each state",
        )

    def test_a_caption_the_pdf_supplied_is_left_alone(self):
        old = "The image illustrates how particles are arranged."
        new = "Particles are arranged differently in each state."

        self.assertIsNone(self.retitle(self._Obj("5. Comparing the Three States"), old, new))

    def test_a_name_a_teacher_chose_is_left_alone(self):
        old = "The image illustrates how particles are arranged."
        new = "Particles are arranged differently in each state."

        self.assertIsNone(self.retitle(self._Obj("Particle arrangement diagram"), old, new))

    def test_an_unchanged_title_is_not_rewritten(self):
        same = "Particles are arranged differently. More detail follows."

        self.assertIsNone(
            self.retitle(self._Obj("Particles are arranged differently"), same, same),
        )
