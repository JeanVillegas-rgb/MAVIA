"""The same comparison, taught as a section in one PDF and a table in others.

"Comparing the Three States" was a text section in PDF 1, a table titled
"Comparing the Three States" in PDF 3, and a table titled "6. SOLID VS.
LIQUID VS. GAS" in PDF 2 -- three concepts, met up to three times. A table
is worded nothing like prose, so the model scored them 0.45-0.52 and nothing
connected them.
"""

import os
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from .models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode
from .services.semantic_grouping import _tables_agree
from .services.unit_matching import refresh_heading_unit_suggestions
from .test_semantic_grouping import FakeRuntime
from .test_unit_matching import SEMANTIC_ENV

PDF2_TABLE = """Property | Solid | Liquid | Gas
Shape | Definite | Not definite Not definite
Volume | Definite | Definite | Not definite
Particle distance | Very close Close | Far apart
Particle movement Vibrate | Slide/move Move freely
Can flow? | No | Yes | Yes
Example | Rock | Water | Air"""
PDF3_TABLE = """Property | Solid | Liquid | Gas
Shape | Fixed | Takes shape of container | Fills entire container
Volume | Fixed | Fixed | Not fixed
Particle spacing | Very close together | Close together | Far apart
Particle movement | Vibrate in place | Slide past each other | Move freely, fast
Example | Ice | Water | Water vapor"""
CHANGES_TABLE = """Change | From | To
Melting | Solid | Liquid
Freezing | Liquid | Solid
Evaporation | Liquid | Gas"""


def table(visible_text, url):
    material = SimpleNamespace(generated_json={"image_descriptions": [
        {"image_url": url, "visible_text": visible_text, "is_table": True},
    ]})
    return SimpleNamespace(material=material, image_url=url)


class TableStructureTests(SimpleTestCase):
    def test_the_same_columns_and_most_row_names_are_the_same_table(self):
        self.assertTrue(_tables_agree(table(PDF2_TABLE, "/a.png"), [table(PDF3_TABLE, "/b.png")]))

    def test_a_different_table_is_not_the_same(self):
        self.assertFalse(_tables_agree(table(PDF2_TABLE, "/a.png"), [table(CHANGES_TABLE, "/b.png")]))

    def test_the_same_columns_with_different_rows_are_not_the_same(self):
        other = "Property | Solid | Liquid | Gas\nColour | Grey | Clear | None\nSmell | None | Sweet | Sharp\nTaste | None | Sweet | None"

        self.assertFalse(_tables_agree(table(PDF3_TABLE, "/a.png"), [table(other, "/b.png")]))

    def test_text_that_is_not_a_table_is_not_a_table(self):
        self.assertFalse(_tables_agree(table("A solid keeps its shape.", "/a.png"), [table(PDF3_TABLE, "/b.png")]))


class TitledTableUnitTests(TestCase):
    """A section and a table that carries its heading as its title."""

    def setUp(self):
        course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.pdf1 = LearningMaterial.objects.create(course=course, outline_node=self.topic, title="1", generated_json=dict(confirmed))
        self.pdf3 = LearningMaterial.objects.create(course=course, outline_node=self.topic, title="3", generated_json=dict(confirmed))

    def add(self, material, title, section, order, kind="text"):
        return LearningObject.objects.create(
            material=material, title=title, section_title=section, content=f"{title} text.", order=order, kind=kind,
            group=LearningObjectGroup.objects.create(outline_node=self.topic, label=title),
        )

    def refresh(self, score):
        runtime = FakeRuntime()
        runtime.pair_scores = lambda pairs: [score for _ in pairs]
        with patch.dict(os.environ, SEMANTIC_ENV):
            return refresh_heading_unit_suggestions(self.topic, runtime_instance=runtime)

    def test_a_table_titled_with_the_sections_heading_joins_it_at_the_review_bar(self):
        shape = self.add(self.pdf1, "Shape", "Comparing the Three States", 0)
        self.add(self.pdf1, "Volume", "Comparing the Three States", 1)
        table_row = self.add(self.pdf3, "Comparing the Three States", "Comparing the Three States", 0, kind="image")

        counts = self.refresh(0.48)

        table_row.refresh_from_db()
        shape.refresh_from_db()
        self.assertEqual(counts["placed"], 1)
        self.assertEqual(table_row.group_id, shape.group_id)

    def test_a_generic_heading_still_needs_the_auto_bar(self):
        first = self.add(self.pdf1, "Stone", "Everyday Examples", 0)
        self.add(self.pdf1, "Ice cube", "Everyday Examples", 1)
        table_row = self.add(self.pdf3, "Everyday Examples", "Everyday Examples", 0, kind="image")

        counts = self.refresh(0.48)

        table_row.refresh_from_db()
        self.assertEqual(counts["placed"], 0)
        self.assertNotEqual(table_row.group_id, first.group_id)
