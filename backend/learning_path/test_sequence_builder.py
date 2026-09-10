from django.test import TestCase

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode

from .services.path_builder import build_generic_sequence


class Fixture(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.module = OutlineNode.objects.create(
            course=self.course, title="Properties of Matter", order=0, depth=0
        )
        self.topic = OutlineNode.objects.create(
            course=self.course, parent=self.module,
            title="Solid, Liquid and Gas", order=0, depth=1,
        )
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic,
            title="States", status="completed",
        )

    def _object(self, order, title, content="Text.", section_title=""):
        return LearningObject.objects.create(
            material=self.material, title=title, content=content,
            section_title=section_title, order=order,
        )


class SequenceBuilderTests(Fixture):
    """The graph says what *must* precede what. Where it is silent, the author
    decides. That is the whole division of labour."""

    def test_the_author_decides_among_chunks_the_graph_does_not_rank(self):
        solid = self._object(0, "Solid")
        liquid = self._object(1, "Liquid")
        gas = self._object(2, "Gas")

        order = build_generic_sequence([solid, liquid, gas], [])

        self.assertEqual(order, [solid.id, liquid.id, gas.id])

    def test_a_prerequisite_always_wins_over_the_authors_order(self):
        """The one case where the graph overrides the PDF: a property defined
        after the passage that needs it."""
        solid = self._object(0, "Solid")
        shape = self._object(1, "Shape")

        order = build_generic_sequence([solid, shape], [(shape.id, solid.id)])

        self.assertEqual(order, [shape.id, solid.id])

    def test_split_parts_stay_together(self):
        first = self._object(0, "Solid (Part 1 of 2)")
        other = self._object(1, "Liquid")
        second = self._object(2, "Solid (Part 2 of 2)")

        order = build_generic_sequence(
            [first, other, second], [(first.id, second.id)]
        )

        self.assertEqual(order.index(second.id), order.index(first.id) + 1)

    def test_a_sections_chunks_are_not_interleaved_when_the_graph_permits(self):
        intro = self._object(0, "Intro", section_title="A")
        other = self._object(1, "Elsewhere", section_title="B")
        more = self._object(2, "More", section_title="A")

        order = build_generic_sequence([intro, other, more], [])

        self.assertLess(order.index(more.id), order.index(other.id))

    def test_every_chunk_appears_exactly_once(self):
        objects = [self._object(index, f"Concept {index}") for index in range(5)]

        order = build_generic_sequence(objects, [(objects[3].id, objects[0].id)])

        self.assertCountEqual(order, [item.id for item in objects])

    def test_a_cycle_is_reported_rather_than_silently_truncated(self):
        from .services.topological_sort import GraphCycleError

        a = self._object(0, "A")
        b = self._object(1, "B")

        with self.assertRaises(GraphCycleError):
            build_generic_sequence([a, b], [(a.id, b.id), (b.id, a.id)])

    def test_the_result_is_reproducible(self):
        objects = [self._object(index, f"Concept {index}") for index in range(6)]
        edges = [(objects[0].id, objects[4].id)]

        first = build_generic_sequence(objects, edges)
        second = build_generic_sequence(list(reversed(objects)), edges)

        self.assertEqual(first, second)
