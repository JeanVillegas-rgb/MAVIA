"""Regression coverage for sentence lead-ins and supporting section parts."""

from django.test import SimpleTestCase

from .services.content_generator import build_section_learning_objects


def block(block_id, text, *, page=1, y=0, **extra):
    return {
        "block_id": block_id,
        "page": page,
        "text": text,
        "line_count": 1,
        "category": "lesson_content",
        "include_in_narration": True,
        "bbox": (72.0, float(y), 520.0, float(y + 14)),
        **extra,
    }


class InlineSentenceLeadinTests(SimpleTestCase):
    def test_key_idea_and_everyday_examples_stay_inside_their_section(self):
        blocks = [
            block(1, "Solids", y=10, is_bold=True),
            block(2, "In a solid, particles are packed tightly together.", y=30),
            block(3, "Everyday examples", y=50, is_bold=True),
            block(
                4,
                "An ice cube keeps its shape whether it sits in a bowl or on a plate.",
                y=70,
            ),
            block(
                5,
                "Key idea: solid particles are held in place by strong forces between them, "
                "so heating makes them vibrate faster.",
                y=90,
                line_count=2,
            ),
        ]

        objects = build_section_learning_objects(blocks, [])

        self.assertEqual([item["title"] for item in objects], ["Solids"])
        self.assertIn("Everyday examples", objects[0]["content"])
        self.assertIn(
            "Key idea: solid particles are held in place by strong forces",
            objects[0]["content"],
        )

    def test_long_colon_leadin_remains_in_the_narrated_sentence(self):
        source = (
            "Matter usually exists in one of three everyday states: "
            "solid, liquid, and gas."
        )

        objects = build_section_learning_objects([block(1, source)], [])

        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0]["content"], source)
        self.assertFalse(objects[0]["content"].startswith("solid,"))

    def test_separate_general_rule_leadin_stays_with_active_concept(self):
        blocks = [
            block(1, "Matter", y=10, is_bold=True),
            block(2, "Particles move differently as their energy changes.", y=30),
            block(3, "As a general rule", y=50, is_bold=True),
            block(4, "the more energy particles have, the faster they move.", y=70),
        ]

        objects = build_section_learning_objects(blocks, [])

        self.assertEqual([item["title"] for item in objects], ["Matter"])
        self.assertIn(
            "As a general rule, the more energy particles have",
            objects[0]["content"],
        )

    def test_real_peer_headings_still_create_separate_concepts(self):
        blocks = [
            block(1, "Solids", y=10, is_bold=True),
            block(2, "A solid has a definite shape and volume.", y=30),
            block(3, "Liquids", y=50, is_bold=True),
            block(4, "A liquid has a definite volume but changes shape.", y=70),
        ]

        objects = build_section_learning_objects(blocks, [])

        self.assertEqual([item["title"] for item in objects], ["Solids", "Liquids"])

    def test_any_bold_leadin_followed_by_lower_case_stays_in_its_section(self):
        blocks = [
            block(1, "Gases", y=10, is_bold=True, font_size=14),
            block(2, "In a gas, particles are spread far apart.", y=30, font_size=11),
            block(3, "Remember", y=50, is_bold=True, font_size=11),
            block(4, "cooling a gas slows its particles down until they pack close.", y=70, font_size=11),
        ]

        objects = build_section_learning_objects(blocks, [])

        self.assertEqual([item["title"] for item in objects], ["Gases"])
        self.assertIn("Remember:", objects[0]["content"])
        self.assertIn("cooling a gas slows its particles", objects[0]["content"])

    def test_any_inline_leadin_followed_by_lower_case_stays_whole(self):
        sentence = "Big idea: cooling a gas slows its particles down until they form a liquid."
        blocks = [
            block(1, "Gases", y=10, is_bold=True, font_size=14),
            block(2, "In a gas, particles are spread far apart.", y=30, font_size=11),
            block(3, sentence, y=50, font_size=11),
        ]

        objects = build_section_learning_objects(blocks, [])

        self.assertEqual([item["title"] for item in objects], ["Gases"])
        self.assertIn(sentence, objects[0]["content"])

    def test_bold_label_before_a_capitalised_sentence_is_still_a_heading(self):
        blocks = [
            block(1, "Gases", y=10, is_bold=True, font_size=14),
            block(2, "In a gas, particles are spread far apart.", y=30, font_size=11),
            block(3, "Condensation", y=50, is_bold=True, font_size=11),
            block(4, "Cooling a gas slows its particles down until they pack close.", y=70, font_size=11),
        ]

        objects = build_section_learning_objects(blocks, [])

        self.assertEqual([item["title"] for item in objects], ["Gases", "Condensation"])

    def test_definition_pair_inside_a_section_still_splits(self):
        blocks = [
            block(1, "States of Matter", y=10, is_bold=True, font_size=14),
            block(2, "Matter exists in three common states.", y=30, font_size=11),
            block(3, "Solid: matter with a fixed shape and volume.", y=50, font_size=11),
            block(4, "Liquid: matter with a fixed volume but no fixed shape.", y=70, font_size=11),
        ]

        objects = build_section_learning_objects(blocks, [])

        self.assertEqual(
            [item["title"] for item in objects],
            ["States of Matter", "Solid", "Liquid"],
        )

    def test_single_definition_of_a_lesson_term_still_splits(self):
        # "Solid" is used again in the lesson's own text, so it is a term being
        # defined, not an author's lead-in like "Remember".
        blocks = [
            block(1, "States of Matter", y=10, is_bold=True, font_size=14),
            block(2, "Matter can be a solid, a liquid or a gas.", y=30, font_size=11),
            block(3, "Solid: matter with a fixed shape and volume.", y=50, font_size=11),
        ]

        objects = build_section_learning_objects(blocks, [])

        self.assertEqual([item["title"] for item in objects], ["States of Matter", "Solid"])

    def test_figure_description_inherits_nearest_text_section(self):
        blocks = [
            block(1, "Solids", y=10, is_bold=True),
            block(2, "A solid has tightly packed particles.", y=30),
        ]
        images = [
            {
                "page": 1,
                "page_number": 1,
                "bbox": (72.0, 50.0, 300.0, 150.0),
                "description": "The diagram shows tightly packed particles in a solid.",
                "content": "The diagram shows tightly packed particles in a solid.",
                "title": "Particle diagram",
            }
        ]

        objects = build_section_learning_objects(blocks, images)
        figure = next(item for item in objects if item["type"] == "image_description")

        self.assertEqual(figure["section_title"], "Solids")


class FigureTitleTests(SimpleTestCase):
    def figure(self, y, **extra):
        return {
            "page": 1,
            "page_number": 1,
            "bbox": (72.0, float(y), 300.0, float(y + 100)),
            "description": "The image shows three arrangements of particles representing solids.",
            "content": "The image shows three arrangements of particles representing solids.",
            **extra,
        }

    def test_uncaptioned_figure_is_named_after_its_section_not_its_description(self):
        blocks = [
            block(1, "Solids", y=10, is_bold=True),
            block(2, "A solid has tightly packed particles.", y=30),
        ]

        objects = build_section_learning_objects(blocks, [self.figure(50)])
        figure = next(item for item in objects if item["type"] == "image_description")

        self.assertEqual(figure["title"], "Solids - figure")
        self.assertNotIn("title_from_section", figure)

    def test_several_uncaptioned_figures_in_one_section_are_numbered(self):
        blocks = [
            block(1, "Solids", y=10, is_bold=True),
            block(2, "A solid has tightly packed particles.", y=30),
        ]

        objects = build_section_learning_objects(
            blocks,
            [self.figure(50), self.figure(200), self.figure(350, is_table=True)],
        )
        titles = [item["title"] for item in objects if item["type"] == "image_description"]

        self.assertEqual(titles, ["Solids - figure 1", "Solids - figure 2", "Solids - table"])

    def test_printed_caption_wins_over_section_name(self):
        blocks = [
            block(1, "Solids", y=10, is_bold=True),
            block(2, "A solid has tightly packed particles.", y=30),
        ]

        objects = build_section_learning_objects(
            blocks,
            [self.figure(50, caption="Figure 1. Particles in a solid")],
        )
        figure = next(item for item in objects if item["type"] == "image_description")

        self.assertEqual(figure["title"], "Particles in a solid")

    def test_figure_with_no_section_falls_back_to_its_description(self):
        objects = build_section_learning_objects([], [self.figure(50)])

        self.assertEqual(
            objects[0]["title"],
            "The image shows three arrangements of particles representing solids",
        )
