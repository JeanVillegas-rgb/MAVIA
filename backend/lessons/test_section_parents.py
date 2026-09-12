"""Authored sections must survive chunking.

A numbered section heading names the concept its sub-headings belong to. When
that parent is dropped, "Diagram description" and "Everyday examples" appear
three times each in one PDF with nothing to tell them apart, and grouping has no
way to place them.
"""

from django.test import SimpleTestCase

from .services.content_generator import (
    _qualifies_as_section_parent,
    balance_learning_object_chunks,
    build_learning_objects_from_pdf_blocks,
)


def item(title, content, section_title, order=0):
    return {
        "type": "lesson_content",
        "section_title": section_title,
        "title": title,
        "content": content,
        "source_page": 4,
        "source_block_id": order + 1,
        "order": order,
    }


def block(block_id, text, **extra):
    return {"block_id": block_id, "page": 1, "text": text, "line_count": 1, **extra}


class SectionParentTests(SimpleTestCase):
    def sections(self, learning_objects):
        return {item["title"]: item["section_title"] for item in learning_objects}

    def test_sub_headings_inherit_their_numbered_section(self):
        blocks = [
            block(1, "2. Solids", is_bold=True, font_size=19),
            block(
                2,
                "In a solid, particles are packed tightly together in a fixed, "
                "orderly pattern. They vibrate in place but do not move past one another.",
                line_count=2,
            ),
            block(
                3,
                "Diagram description: particles in a solid are drawn as evenly "
                "spaced dots arranged in tidy rows and columns, like a grid.",
                line_count=2,
            ),
            block(
                4,
                "Everyday examples: ice cubes, a wooden chair, a rock, a coin, and a book.",
            ),
        ]

        sections = self.sections(build_learning_objects_from_pdf_blocks(blocks, []))

        self.assertEqual(sections.get("Diagram description"), "Solids")
        self.assertEqual(sections.get("Everyday examples"), "Solids")

    def test_a_new_numbered_section_replaces_the_previous_one(self):
        blocks = [
            block(1, "2. Solids", is_bold=True, font_size=19),
            block(2, "In a solid, particles are packed tightly together in a fixed pattern.", line_count=2),
            block(3, "Everyday examples: ice cubes, a wooden chair, and a rock."),
            block(4, "3. Liquids", is_bold=True, font_size=19),
            block(5, "In a liquid, particles are close together but can slide past each other.", line_count=2),
            block(6, "Diagram description: dots drawn close together in an irregular arrangement.", line_count=2),
        ]

        objects = build_learning_objects_from_pdf_blocks(blocks, [])
        by_order = [(item["title"], item["section_title"]) for item in objects]

        self.assertIn(("Diagram description", "Liquids"), by_order)
        self.assertNotIn(("Diagram description", "Solids"), by_order)


class SectionParentEligibilityTests(SimpleTestCase):
    """A numbered heading is not automatically a section.

    ``_learning_object_heading_title`` returns a numbered title before it checks
    category, so quiz items and answer keys reach the heading path too.
    """

    def test_authored_instructional_heading_opens_a_section(self):
        self.assertTrue(
            _qualifies_as_section_parent(
                {"text": "2. Solids", "category": "lesson_content", "include_in_narration": True}
            )
        )

    def test_assessment_item_never_opens_a_section(self):
        for text in ("1. ______ condense", "8. Quick Check Questions", "9. Answer Key"):
            self.assertFalse(
                _qualifies_as_section_parent(
                    {"text": text, "category": "assessment", "include_in_narration": False}
                ),
                text,
            )

    def test_unnarrated_heading_never_opens_a_section(self):
        self.assertFalse(
            _qualifies_as_section_parent(
                {"text": "5. Comparing", "category": "lesson_content", "include_in_narration": False}
            )
        )

    def test_plain_heading_without_a_number_never_opens_a_section(self):
        self.assertFalse(
            _qualifies_as_section_parent(
                {"text": "Key properties", "category": "lesson_content", "include_in_narration": True}
            )
        )


class FlatDefinitionListTests(SimpleTestCase):
    """Peer definitions must not adopt one of their own as a parent."""

    def test_a_glossary_term_does_not_become_the_section_of_its_peers(self):
        # A page footer between two terms breaks the sibling run, which is how
        # the first term used to be promoted to the parent of the rest.
        blocks = [
            block(1, "gas\n\x07matter that can freely change shape and size", line_count=2),
            block(2, "liquid\n\x07matter that keeps its size but takes the shape of its container", line_count=2),
            block(3, "physical change\n\x07a change in the size, shape, or color of a substance", line_count=2),
            block(4, "© Learning A-Z All rights reserved. www.sciencea-z.com 4"),
            block(5, "property\n\x07a feature or quality that can be used to describe something", line_count=2),
            block(6, "solid\n\x07matter that keeps its shape and size", line_count=2),
        ]

        objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertTrue(objects, "the glossary produced no learning objects")
        self.assertEqual(
            {item["section_title"] for item in objects},
            {""},
            "a peer definition was promoted to the section of the others",
        )


class SectionVariantMergeTests(SimpleTestCase):
    """A heading and its short labelled items are one teaching unit.

    They teach by contrast, so they share almost no wording and adjacent
    similarity cannot see the relationship. The shared section can.
    """

    SECTION = "PARTICLE ARRANGEMENT"

    def family(self):
        return [
            item(self.SECTION, "The particles in each state of matter behave differently.", self.SECTION, 0),
            item("Solid", "Particles are very close together. They mostly vibrate in place.", self.SECTION, 1),
            item("Liquid", "Particles are close but can move around. They can slide past each other.", self.SECTION, 2),
            item("Gas", "Particles are widely separated. They move freely in different directions.", self.SECTION, 3),
        ]

    def test_heading_and_its_contrasting_items_become_one_object(self):
        balanced = balance_learning_object_chunks(self.family())

        self.assertEqual(len(balanced), 1)
        self.assertEqual(balanced[0]["title"], self.SECTION)
        self.assertEqual(balanced[0]["chunk_count"], 4)
        self.assertIn("Solid: Particles are very close together", balanced[0]["content"])
        self.assertIn("Liquid: Particles are close but can move around", balanced[0]["content"])
        self.assertIn("Gas: Particles are widely separated", balanced[0]["content"])

    def test_the_section_label_is_no_longer_carried_by_three_objects(self):
        # The duplicated "Solid"/"Liquid"/"Gas" labels are what made those
        # labels ambiguous inside one PDF.
        balanced = balance_learning_object_chunks(self.family())

        self.assertNotIn("Solid", [row["title"] for row in balanced])

    def test_substantial_items_under_one_section_are_left_alone(self):
        body = " ".join(["A solid keeps a definite shape and a definite volume."] * 6)
        objects = [
            item("SOLID (Part 1 of 3)", body, "SOLID", 0),
            item("SOLID (Part 2 of 3)", body, "SOLID", 1),
        ]

        balanced = balance_learning_object_chunks(objects)

        self.assertEqual(len(balanced), 2)

    def test_items_without_a_section_are_left_alone(self):
        objects = [
            item("Solid", "Particles are very close together. They mostly vibrate in place.", "", 0),
            item("Liquid", "Particles are close but can move around. They slide past each other.", "", 1),
        ]

        balanced = balance_learning_object_chunks(objects)

        self.assertEqual(len(balanced), 2)

    def test_a_container_section_keeps_its_entries_separate(self):
        # "Vocabulary" collects unrelated terms that each stand alone. Only a
        # section naming one concept may absorb its items.
        for section in ("Vocabulary", "Glossary", "Everyday Examples"):
            objects = [
                item("Mass", "Amount of matter in an object.", section, 0),
                item("Texture", "How a surface feels when touched.", section, 1),
            ]

            balanced = balance_learning_object_chunks(objects)

            self.assertEqual([row["title"] for row in balanced], ["Mass", "Texture"], section)

    def test_a_long_neighbour_is_not_pulled_into_the_run(self):
        # Over the variant limit but under LEARNING_OBJECT_MAX_WORDS, so the
        # neighbour is neither merged in nor split apart.
        long_body = " ".join(["Particles in a solid are drawn as evenly spaced dots in tidy rows."] * 3)
        objects = [
            item("Solids", "In a solid, particles are packed tightly together.", "Solids", 0),
            item("Diagram description", long_body, "Solids", 1),
        ]

        balanced = balance_learning_object_chunks(objects)

        self.assertEqual(len(balanced), 2)
