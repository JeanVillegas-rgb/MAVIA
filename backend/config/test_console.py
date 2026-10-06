"""The terminal line format every pipeline shares."""

import logging

from django.test import SimpleTestCase

from config.console import ConsoleFormatter, name, percent


def _record(level, message):
    return logging.LogRecord("lessons", level, __file__, 1, message, None, None)


class ConsoleFormatterTests(SimpleTestCase):
    def test_an_info_line_is_time_then_message(self):
        line = ConsoleFormatter().format(_record(logging.INFO, "[Grouping] done"))

        self.assertRegex(line, r"^\d\d:\d\d:\d\d  \[Grouping\] done$")

    def test_a_warning_says_so_before_the_message(self):
        line = ConsoleFormatter().format(_record(logging.WARNING, "[Publish] retrying"))

        self.assertRegex(line, r"^\d\d:\d\d:\d\d  WARNING  \[Publish\] retrying$")


class HelperTests(SimpleTestCase):
    def test_a_long_title_is_cut_to_one_line(self):
        quoted = name("word " * 40, limit=20)

        self.assertLessEqual(len(quoted), 22)
        self.assertTrue(quoted.endswith('…"'))

    def test_a_score_reads_as_a_percentage(self):
        self.assertEqual(percent(0.4897), "49%")
