"""Extraction bugs found by running the uploaded PDFs through the pipeline.

Each case is the shape measured on a real PDF: the Solid, Liquid and Gas
handouts, the fern and flower modules, and the Grade 8 English handout.
"""

from django.test import SimpleTestCase, TestCase

from .models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode
from .services.content_generator import (
    _accessible_narration_text,
    _split_sentences,
    build_section_learning_objects,
    disambiguate_repeated_titles,
)
from .services.image_describer import (
    _extracted_captions,
    _extracted_stand_ins,
    _still_the_extracted_caption,
    spoken_table,
)
from .services.instructional_content_classifier import classify_instructional_blocks
from .services.learning_resource_linker import attach_orphan_objects_to_their_section


def text_block(block_id, text, *, page=1, y=0, bold=False, size=11.0, lines=1):
    return {
        "block_id": block_id, "page": page, "text": text, "line_count": lines,
        "is_bold": bold, "font_size": size, "bbox": (60.0, float(y), 520.0, float(y + 14)),
    }


def classified(blocks):
    return classify_instructional_blocks(blocks)


class TableSectionTests(SimpleTestCase):
    """A section that is only a heading and a table."""

    def table(self, y, title):
        return {
            "page": 5, "page_number": 5, "bbox": (74.0, float(y), 344.0, float(y + 200)),
            "title": title, "content": "", "description": "", "is_table": True,
        }

    def test_the_table_takes_the_heading_printed_above_it(self):
        blocks = [
            text_block(1, "5. Particle Arrangement", page=5, y=40, bold=True, size=14),
            text_block(2, "Particles in a gas are widely separated and move freely.", page=5, y=70),
            text_block(3, "6. Solid vs. Liquid vs. Gas", page=5, y=330, bold=True, size=14),
            text_block(4, "7. Changes in States of Matter", page=5, y=608, bold=True, size=14),
            text_block(5, "Matter changes state when heat is added or removed from it.", page=5, y=630),
        ]

        objects = build_section_learning_objects(
            classified(blocks), [self.table(360, "6. Solid vs. Liquid vs. Gas")],
        )
        table = next(item for item in objects if item["type"] == "image_description")

        self.assertEqual(table["section_title"], "Solid vs. Liquid vs. Gas")


class TitleBlockTests(SimpleTestCase):
    def test_a_subject_lesson_grade_line_is_not_lesson_text(self):
        result = classified([
            text_block(1, "States of Matter", y=10, bold=True, size=17),
            text_block(2, "Science · Lesson 1 · Grade 4", y=40, size=10),
            text_block(3, "Matter is anything that has mass and takes up space in the world.", y=70),
        ])

        self.assertEqual(result[1]["category"], "document_metadata")

    def test_a_subtitle_printed_with_the_title_is_set_aside(self):
        result = classified([
            text_block(1, "States of Matter\nSolid, Liquid, and Gas", y=10, bold=True, size=17, lines=2),
            text_block(2, "Matter is anything that has mass and takes up space in the world.", y=70),
        ])

        self.assertEqual(result[0]["text"], "States of Matter")
        self.assertEqual(result[0]["title_block_lines"], ["Solid, Liquid, and Gas"])
        objects = build_section_learning_objects(result, [])
        self.assertEqual(objects[0]["title"], "States of Matter")
        self.assertNotIn("Solid, Liquid, and Gas", objects[0]["content"])

    def test_a_short_line_after_the_first_paragraph_is_untouched(self):
        result = classified([
            text_block(1, "States of Matter", y=10, bold=True, size=17),
            text_block(2, "Matter is anything that has mass and takes up space in the world.", y=40),
            text_block(3, "Solids and liquids", y=70),
        ])

        self.assertNotEqual(result[2]["reason"], "Subtitle in the document's title block, above the first paragraph.")


class QuestionHeadingTests(SimpleTestCase):
    """"What is Point of View?" over its definition, measured on the English handout."""

    def test_the_paragraph_answering_a_question_heading_is_kept(self):
        blocks = [
            text_block(1, "Part 3: Understanding Point of View (POV)", y=10, bold=True, size=12),
            text_block(2, "What is Point of View?", y=40, bold=True, size=14),
            text_block(3, "Point of view is the angle or perspective from which a story is told.", y=70, lines=2),
            text_block(4, "First Person POV", y=110, bold=True, size=12),
            text_block(5, "Told by the protagonist or a character closely involved in the story.", y=140, lines=2),
        ]

        objects = build_section_learning_objects(classified(blocks), [])
        by_title = {item["title"]: item for item in objects}

        self.assertIn("What is Point of View?", by_title)
        self.assertIn("angle or perspective", by_title["What is Point of View?"]["content"])
        # The "Part 3:" label names the section of everything under it, even
        # though it is printed smaller than the headings inside it.
        self.assertEqual(by_title["First Person POV"]["section_title"], "Understanding Point of View (POV)")

    def test_a_quiz_question_followed_by_choices_is_still_an_assessment(self):
        result = classified([
            text_block(1, "Which state has a fixed shape?", y=10, bold=True, size=12),
            text_block(2, "A. Solid B. Liquid C. Gas", y=40),
        ])

        self.assertEqual(result[0]["category"], "assessment")


class ContentsListTests(SimpleTestCase):
    """A contents page names lesson sections; it does not teach their content."""

    def test_contents_block_does_not_absorb_the_first_numbered_section(self):
        blocks = [
            text_block(1, "About this guide: This guide explains the lesson layout and navigation.", y=10),
            text_block(2, "In This Guide", y=40, bold=True, size=14),
            text_block(3, "1.\u200b What is matter?\n2.\u200b Solids\n3.\u200b Liquids", y=70, lines=3),
            text_block(4, "1. What Is Matter?", y=140, bold=True, size=14),
            text_block(5, "Matter is anything that takes up space and has mass, such as air and water.", y=170, lines=2),
            text_block(6, "2. Solids", y=210, bold=True, size=14),
            text_block(7, "A solid keeps its shape because its particles stay close together.", y=240, lines=2),
        ]

        result = classified(blocks)
        self.assertEqual([result[1]["category"], result[2]["category"]], ["navigation", "navigation"])
        self.assertEqual(result[3]["category"], "lesson_content")
        objects = build_section_learning_objects(result, [])
        by_title = {item["title"]: item for item in objects}
        self.assertNotIn("In This Guide", by_title)
        self.assertIn("What Is Matter?", by_title)
        self.assertIn("Matter is anything", by_title["What Is Matter?"]["content"])
        self.assertNotIn("2.\u200b Solids", by_title["What Is Matter?"]["content"])

    def test_separate_contents_entries_are_not_section_headings(self):
        blocks = [
            text_block(1, "In This Guide", y=10, bold=True, size=14),
            text_block(2, "1. What is matter?", y=30),
            text_block(3, "2. Solids", y=50),
            text_block(4, "3. Liquids", y=70),
            text_block(5, "1. What Is Matter?", y=100, bold=True, size=14),
            text_block(6, "Matter is anything that takes up space and has mass, such as air and water.", y=130, lines=2),
        ]

        result = classified(blocks)
        self.assertEqual([block["category"] for block in result[:4]], ["navigation"] * 4)
        objects = build_section_learning_objects(result, [])
        self.assertEqual([item["title"] for item in objects], ["What Is Matter?"])

    def test_numbered_quiz_prompt_remains_an_assessment(self):
        result = classified([
            text_block(1, "1. What is matter?", y=10, bold=True, size=11),
            text_block(2, "Matter is anything that takes up space and has mass, such as air and water.", y=40, size=11, lines=2),
        ])
        self.assertEqual(result[0]["category"], "assessment")


class DashLeadinTests(SimpleTestCase):
    def test_a_dash_leadin_under_a_heading_keeps_the_heading_and_the_sentence(self):
        sentence = (
            "Flowering plants rely on pollination — pollen must travel from the anther "
            "to the stigma before fertilization can occur."
        )
        blocks = [
            text_block(1, "Comparing With Flowering Plants", y=10, bold=True, size=14),
            text_block(2, sentence, y=40, lines=2),
        ]

        objects = build_section_learning_objects(classified(blocks), [])

        self.assertEqual(objects[0]["title"], "Comparing With Flowering Plants")
        self.assertTrue(objects[0]["content"].startswith("Flowering plants rely on pollination"))


class AbbreviationTests(SimpleTestCase):
    def test_an_abbreviation_does_not_end_a_sentence(self):
        self.assertEqual(
            _split_sentences("• Character vs. Nature — The character struggles. Another sentence."),
            ["• Character vs. Nature — The character struggles.", "Another sentence."],
        )
        self.assertEqual(len(_split_sentences("Use water, e.g. Rain water. Then boil it.")), 2)

    def test_a_list_number_is_not_a_sentence_end(self):
        self.assertEqual(_split_sentences("Step 1. Heat it. Then cool it."), ["Step 1. Heat it.", "Then cool it."])


class RepeatedTitleTests(SimpleTestCase):
    def test_a_repeated_name_gets_its_section(self):
        objects = [
            {"title": "SOLID (Part 1 of 2)", "section_title": "SOLID"},
            {"title": "SOLID (Part 2 of 2)", "section_title": "SOLID"},
            {"title": "PARTICLE ARRANGEMENT", "section_title": "PARTICLE ARRANGEMENT"},
            {"title": "Solid", "section_title": "PARTICLE ARRANGEMENT"},
            {"title": "Shape", "section_title": "Comparing"},
        ]

        titles = [item["title"] for item in disambiguate_repeated_titles(objects)]

        self.assertEqual(titles, [
            "SOLID (Part 1 of 2)", "SOLID (Part 2 of 2)", "PARTICLE ARRANGEMENT",
            "Solid (Particle Arrangement)", "Shape",
        ])

    def test_a_heading_other_objects_name_as_their_section_is_never_renamed(self):
        objects = [
            {"title": "Solid", "section_title": "Matter"},
            {"title": "Solid", "section_title": "States"},
            {"title": "Solid particles", "section_title": "Solid"},
        ]

        titles = [item["title"] for item in disambiguate_repeated_titles(objects)]

        self.assertEqual(titles[:2], ["Solid", "Solid"])


class SpokenFormTests(SimpleTestCase):
    def test_list_items_end_with_a_full_stop(self):
        spoken = _accessible_narration_text(
            "Characteristics of Solids:\n• Has a definite shape\n• Has a definite volume"
        )

        self.assertEqual(spoken, "Characteristics of Solids:\nHas a definite shape.\nHas a definite volume.")

    def test_a_wrapped_sentence_is_not_broken(self):
        spoken = _accessible_narration_text("Evaporation occurs when particles\nat the surface gain energy.")

        self.assertEqual(spoken, "Evaporation occurs when particles\nat the surface gain energy.")

    def test_equals_sign_reads_as_means_except_in_arithmetic(self):
        self.assertEqual(_accessible_narration_text("SOLID = keeps its shape"), "SOLID means keeps its shape.")
        self.assertEqual(_accessible_narration_text("2 + 3 = 5"), "2 + 3 equals 5.")


class SpokenTableTests(SimpleTestCase):
    def test_a_table_is_read_row_by_row(self):
        text = "Property | Solid | Liquid | Gas\nShape | Definite | Not definite | Not definite"

        self.assertEqual(
            spoken_table(text),
            "Shape: Solid, Definite. Liquid, Not definite. Gas, Not definite.",
        )

    def test_a_row_with_merged_cells_is_read_without_column_names(self):
        text = "Property | Solid | Liquid | Gas\nShape | Definite | Not definite Not definite"

        self.assertEqual(spoken_table(text), "Shape: Definite, Not definite Not definite.")


class TableStandInPublishTests(TestCase):
    def test_a_table_still_on_its_row_reading_is_narrated_at_publish(self):
        course = CourseGroup.objects.create(title="Science")
        topic = OutlineNode.objects.create(course=course, title="Matter", order=0, depth=0)
        reading = "Shape: Solid, Definite. Liquid, Not definite."
        material = LearningMaterial.objects.create(
            course=course, outline_node=topic, title="Module",
            generated_json={"image_descriptions": [
                {"image_url": "/media/table.png", "caption": "", "stand_in": reading},
            ]},
        )
        table = LearningObject.objects.create(
            material=material, kind=LearningObject.Kind.IMAGE, title="Table",
            content=reading, image_url="/media/table.png",
        )

        self.assertTrue(
            _still_the_extracted_caption(table, _extracted_captions(material), _extracted_stand_ins(material))
        )
        table.content = "A teacher's own description of the table."
        self.assertFalse(
            _still_the_extracted_caption(table, _extracted_captions(material), _extracted_stand_ins(material))
        )


class NumberedStepsTests(TestCase):
    """Steps no other PDF teaches are folded into their section's concept."""

    def test_uncorroborated_steps_join_their_section(self):
        course = CourseGroup.objects.create(title="Science")
        node = OutlineNode.objects.create(course=course, title="Reproduction")
        material = LearningMaterial.objects.create(course=course, outline_node=node, title="Ferns")
        section = "How Non-Flowering Plants Reproduce"
        head = LearningObject.objects.create(
            material=material, title=section, section_title="", content="Intro and step one.",
            order=0, group=LearningObjectGroup.objects.create(outline_node=node, label=section),
        )
        steps = [
            LearningObject.objects.create(
                material=material, title=title, section_title=section, content=f"{title} text.",
                order=order, group=LearningObjectGroup.objects.create(outline_node=node, label=title),
            )
            for order, title in enumerate(("Spore dispersal", "Germination and growth"), start=1)
        ]

        attach_orphan_objects_to_their_section(material)

        for step in steps:
            step.refresh_from_db()
            self.assertEqual(step.group_id, head.group_id)

    def test_steps_under_a_heading_with_no_text_of_its_own_stay_together(self):
        """"How Flowering Plants Reproduce" is only a heading over steps 1-4."""
        course = CourseGroup.objects.create(title="Science")
        node = OutlineNode.objects.create(course=course, title="Reproduction")
        material = LearningMaterial.objects.create(course=course, outline_node=node, title="Flowers")
        section = "How Flowering Plants Reproduce"
        steps = [
            LearningObject.objects.create(
                material=material, title=title, section_title=section, content=f"{title} text.",
                order=order, group=LearningObjectGroup.objects.create(outline_node=node, label=title),
            )
            for order, title in enumerate(("Pollination", "Fertilization", "Seed formation"))
        ]

        attach_orphan_objects_to_their_section(material)

        groups = set()
        for step in steps:
            step.refresh_from_db()
            groups.add(step.group_id)
        self.assertEqual(groups, {steps[0].group_id})

    def test_a_step_another_pdf_teaches_stays_its_own_concept(self):
        course = CourseGroup.objects.create(title="Science")
        node = OutlineNode.objects.create(course=course, title="Reproduction")
        material = LearningMaterial.objects.create(course=course, outline_node=node, title="Flowers")
        other = LearningMaterial.objects.create(course=course, outline_node=node, title="Other PDF")
        section = "How Flowering Plants Reproduce"
        pollination_group = LearningObjectGroup.objects.create(outline_node=node, label="Pollination")
        pollination = LearningObject.objects.create(
            material=material, title="Pollination", section_title=section, content="Pollen moves.",
            order=0, group=pollination_group,
        )
        LearningObject.objects.create(
            material=other, title="Pollination", content="Pollen travels.", order=0, group=pollination_group,
        )
        fertilization = LearningObject.objects.create(
            material=material, title="Fertilization", section_title=section, content="Cells join.",
            order=1, group=LearningObjectGroup.objects.create(outline_node=node, label="Fertilization"),
        )

        attach_orphan_objects_to_their_section(material)

        pollination.refresh_from_db()
        fertilization.refresh_from_db()
        self.assertEqual(pollination.group_id, pollination_group.id)
        self.assertNotEqual(fertilization.group_id, pollination_group.id)


class RecapSectionTests(SimpleTestCase):
    def test_summary_and_recap_headings_are_recaps(self):
        from .services.content_generator import is_recap_section

        for title, section in (
            ("Summary: what to remember (Part 1 of 2)", "Summary: what to remember"),
            ("Key Takeaways", ""),
            ("What I Have Learned", ""),
            ("Conclusion", ""),
            ("Solids are rigid", "Lesson Summary"),
        ):
            with self.subTest(title=title):
                self.assertTrue(is_recap_section(title, section))

    def test_concepts_and_lead_ins_are_not_recaps(self):
        from .services.content_generator import is_recap_section

        for title in ("Solids", "Key idea", "Key Points", "Remember", "Summer Activities", "Comparing the Three States"):
            with self.subTest(title=title):
                self.assertFalse(is_recap_section(title, ""))


class RecapGroupingTests(TestCase):
    """PDF 2's summary scored close to PDF 3's *Solids* and was paired with it."""

    SUMMARY = (
        "Matter is made of particles and commonly exists as a solid, liquid, or gas. Solids hold "
        "a fixed shape and volume because their particles are tightly packed."
    )
    SOLIDS = (
        "In a solid, particles are packed tightly together in a fixed pattern, so a solid keeps "
        "a definite shape and a definite volume."
    )

    def setUp(self):
        import os
        from unittest.mock import patch

        from .test_label_corroboration import LABEL_ENV

        env = patch.dict(os.environ, LABEL_ENV)
        env.start()
        self.addCleanup(env.stop)
        course = CourseGroup.objects.create(title="Science")
        self.node = OutlineNode.objects.create(course=course, title="States of matter")
        self.pdf2 = LearningMaterial.objects.create(
            course=course, outline_node=self.node, title="PDF 2",
            generated_json={"learning_objects_confirmed": True},
        )
        self.pdf3 = LearningMaterial.objects.create(
            course=course, outline_node=self.node, title="PDF 3",
            generated_json={"learning_objects_confirmed": True},
        )

    def add(self, material, title, section, content):
        return LearningObject.objects.create(
            material=material, title=title, section_title=section, content=content,
            group=LearningObjectGroup.objects.create(outline_node=self.node, label=title),
        )

    def decide(self, source, scores):
        from unittest.mock import patch

        from .services import semantic_grouping as semantic
        from .test_semantic_grouping import FakeRuntime

        with patch.object(semantic, "runtime", return_value=FakeRuntime(scores)):
            return semantic.semantic_decision(
                source.material, source.title, source.content, source.kind, source.order,
                source.section_title, source.id,
            )

    def test_a_summary_is_never_paired_with_a_section_it_restates(self):
        summary = self.add(self.pdf2, "Summary: what to remember (Part 1 of 2)", "Summary: what to remember", self.SUMMARY)
        solids = self.add(self.pdf3, "Solids", "", self.SOLIDS)

        self.assertIsNone(self.decide(summary, {solids.content: .95}))

    def test_nothing_is_paired_into_a_summarys_concept(self):
        summary = self.add(self.pdf2, "Summary: what to remember (Part 1 of 2)", "Summary: what to remember", self.SUMMARY)
        solids = self.add(self.pdf3, "Solids", "", self.SOLIDS)

        self.assertIsNone(self.decide(solids, {summary.content: .95}))

    def test_the_summarys_two_parts_stay_one_step(self):
        part1 = self.add(self.pdf2, "Summary: what to remember (Part 1 of 2)", "Summary: what to remember", self.SUMMARY)
        part2 = self.add(self.pdf2, "Summary: what to remember (Part 2 of 2)", "Summary: what to remember", "Gases spread out.")
        part2.order = 1
        part2.save(update_fields=["order"])

        attach_orphan_objects_to_their_section(self.pdf2)

        part2.refresh_from_db()
        self.assertEqual(part2.group_id, part1.group_id)

    def test_control_the_same_score_pairs_two_ordinary_sections(self):
        # Proves the two tests above fail for the right reason: without a
        # summary involved, this score does pair the sections.
        other = self.add(self.pdf2, "Solid", "", self.SUMMARY)
        solids = self.add(self.pdf3, "Solids", "", self.SOLIDS)

        decision = self.decide(other, {solids.content: .95})

        self.assertIsNotNone(decision)
        self.assertEqual(decision["candidate"].id, solids.id)
