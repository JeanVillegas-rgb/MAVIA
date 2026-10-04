"""A sub-heading names its parent; a sibling merely brushes against it.

`_heading_refers_to_current_concept` absorbs a heading that shares ANY word with
the open concept. That is right for "Examples of Solids" under "Solids", which
contains the whole parent title. It is wrong for "How Matter Changes State"
under "Comparing the Three States", which shares only "state" -- and that is why
Melting, Freezing, Evaporation and Condensation inherited the wrong section.
"""

from django.test import SimpleTestCase

from .services.content_generator import build_learning_objects_from_pdf_blocks


def block(block_id, text, line_count=1, **extra):
    return {"block_id": block_id, "page": 1, "text": text, "line_count": line_count, **extra}


class PeerHeadingTests(SimpleTestCase):
    def titles(self, objects):
        return [item["title"] for item in objects]

    def test_a_heading_sharing_one_word_is_not_a_sub_heading(self):
        blocks = [
            block(1, "Comparing the Three States", is_bold=True),
            block(2, "The table below sets the three states side by side."),
            block(3, "How Matter Changes State", is_bold=True),
            block(4, "Matter can change from one state to another when heat is added."),
        ]

        objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(
            self.titles(objects),
            ["Comparing the Three States", "How Matter Changes State"],
        )

    def test_a_sub_heading_naming_its_parent_is_still_absorbed(self):
        blocks = [
            block(1, "Solids", is_bold=True),
            block(2, "In a solid, particles are packed tightly together."),
            block(3, "Examples of Solids", is_bold=True),
            block(4, "An ice cube, a wooden block, and a rock are all solids."),
        ]

        objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(self.titles(objects), ["Solids"])
        self.assertIn("Examples of Solids:", objects[0]["content"])

    def test_two_headings_sharing_no_word_are_unaffected(self):
        blocks = [
            block(1, "Solids", is_bold=True),
            block(2, "In a solid, particles are packed tightly together."),
            block(3, "Liquids", is_bold=True),
            block(4, "In a liquid, particles slide past one another freely."),
        ]

        objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(self.titles(objects), ["Solids", "Liquids"])

    def test_a_sub_heading_styled_exactly_like_its_parent_is_still_absorbed(self):
        # A plainly-formatted PDF renders every heading at one size and weight,
        # with no colour. Nothing visual distinguishes parent from child, so the
        # decision has to come from the titles themselves.
        #
        # The child is deliberately NOT an "Examples of ..." heading: that form
        # is absorbed by _current_concept_accepts_supporting_component before
        # containment is ever consulted, so a test using it would pass with this
        # rule disabled and pin nothing. "Particles in a Solid" has no such
        # shortcut -- only containment can absorb it.
        flat = {"font_size": 14.0, "is_bold": True, "text_color": 0}
        body = {"font_size": 11.0, "is_bold": False, "text_color": 0}
        blocks = [
            block(1, "Solids", **flat),
            block(2, "In a solid, particles are packed tightly together.", **body),
            block(3, "Particles in a Solid", **flat),
            block(4, "Particles in a solid vibrate in place but do not move past each other.", **body),
        ]

        objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(self.titles(objects), ["Solids"])

    def test_containment_survives_a_plural_parent(self):
        # "Solids" folds to "solid", so a child naming "Solid" still contains it.
        blocks = [
            block(1, "Solids", is_bold=True),
            block(2, "In a solid, particles are packed tightly together."),
            block(3, "Particles in a Solid", is_bold=True),
            block(4, "Particles in a solid vibrate in place but do not move past each other."),
        ]

        objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(self.titles(objects), ["Solids"])

    def test_a_one_word_parent_absorbs_a_heading_that_names_it(self):
        # Soft spot, pinned deliberately: a single-word parent is contained by
        # anything mentioning it, so "States of Matter" folds into "Matter".
        # Arguably they are siblings. If that is ever judged wrong, this is the
        # test to change -- the rule is doing exactly what it says.
        blocks = [
            block(1, "Matter", is_bold=True),
            block(2, "Matter is anything that has mass and takes up space."),
            block(3, "States of Matter", is_bold=True),
            block(4, "Matter is found as a solid, a liquid, or a gas in everyday life."),
        ]

        objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(self.titles(objects), ["Matter"])
