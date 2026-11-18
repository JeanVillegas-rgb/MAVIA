from django.test import SimpleTestCase

from lessons.services.course_outline_parser import parse_outline_lines
from lessons.services.outline_pdf_extractor import rows_from_page_blocks


class CourseOutlineParserTests(SimpleTestCase):
    def test_unicode_dashes_are_supported(self):
        nodes = parse_outline_lines([
            "Module 1 \u2013 Matter",
            "Topic 1 \u2014 Solids",
        ])

        self.assertEqual(nodes[0].title, "Matter")
        self.assertEqual(nodes[0].children[0].title, "Solids")

    def test_returns_mavia_outline_tree(self):
        nodes = parse_outline_lines([
            "Module 1: Matter",
            "1.1 Solids",
            "1.2 Liquids",
            "Module 2: Energy",
            "Topic 1: Heat",
        ])

        self.assertEqual([node.title for node in nodes], ["Matter", "Energy"])
        self.assertEqual(
            [node.title for node in nodes[0].children],
            ["Solids", "Liquids"],
        )
        self.assertEqual(nodes[1].children[0].depth, 1)

    def test_wrapped_titles_inside_one_pdf_block_are_joined(self):
        rows = rows_from_page_blocks(1, [
            (100, 10, 260, 40, "Module 1: Parts and Functions of\nHuman Being\n"),
            (100, 50, 250, 80, "\u2022 Lesson 1: Human Sense\nOrgans\n"),
        ])

        self.assertEqual(rows[0]["text"], "Module 1: Parts and Functions of Human Being")
        self.assertEqual(rows[1]["text"], "\u2022 Lesson 1: Human Sense Organs")

    def test_aligned_neighboring_blocks_can_continue_one_title(self):
        rows = rows_from_page_blocks(1, [
            (100, 10, 240, 22, "\u2022 Lesson 2: Contents of\n"),
            (100, 35, 270, 58, "Elementary Science in a Spiral\nProgress\n"),
        ])

        self.assertEqual(
            rows[0]["text"],
            "\u2022 Lesson 2: Contents of Elementary Science in a Spiral Progress",
        )

    def test_text_in_another_column_is_not_joined_to_a_title(self):
        rows = rows_from_page_blocks(1, [
            (100, 10, 240, 22, "\u2022 Lesson 1: Matter\n"),
            (360, 25, 500, 45, "Worksheet activity\n"),
        ])

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["text"], "\u2022 Lesson 1: Matter")
