"""The direction votes as a spreadsheet (v7 spec section 7)."""

import csv
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

from .services.direction_sheet import column, direction_sheet_rows
from .test_direction_votes import three_states


class DirectionSheetTests(SimpleTestCase):
    def setUp(self):
        self.rows = direction_sheet_rows(three_states())
        self.header = next(index for index, row in enumerate(self.rows) if row[:2] == ["A", "B"])
        self.pairs = {tuple(row[:2]): row for row in self.rows[self.header + 1:]}

    def test_column_letters(self):
        self.assertEqual([column(1), column(26), column(27)], ["A", "Z", "AA"])

    def test_the_matrix_rows_hold_zeros_and_ones(self):
        self.assertEqual(self.rows[5], ["Solid [1]", 1, 1, 1, 0, 0, 0])
        self.assertEqual(self.rows[6], ["Matter [2]", 1, 1, 1, 1, 1, 1])

    def test_counts_are_formulas_over_the_matrix(self):
        row = self.pairs[("Solid [1]", "Matter [2]")]

        self.assertEqual(row[4], "=SUM($B$6:$G$6)")
        self.assertEqual(row[6], "=SUMPRODUCT($B$6:$G$6,$B$7:$G$7)")
        self.assertIn("$C$1", row[11])

    def test_the_python_columns_match_decide_pairs(self):
        row = self.pairs[("Solid [1]", "Matter [2]")]

        self.assertEqual((row[12], row[18], row[19]), (1, "accepted", "B first"))

    def test_the_command_writes_a_csv_excel_can_open(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "sheet.csv"
            command = "learning_path.management.commands.export_direction_sheet"
            with patch(f"{command}.OutlineNode") as topics,                  patch(f"{command}.concepts_for_topic", return_value=three_states()):
                topics.objects.filter.return_value.first.return_value = object()
                call_command("export_direction_sheet", 12, output=str(output))

            with output.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))

        self.assertEqual(rows[0][:3], ["Settings", "MIN_BLOCKS", "3"])
        self.assertTrue(any(row[:2] == ["A", "B"] for row in rows))
