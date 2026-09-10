"""Behaviours that survived the move from vote-scoring to evidence tiers.

The scoring tests that lived here are gone with the model they tested; their
replacements are in ``test_evidence.py``. What remains are the two rules that
are independent of how evidence is combined.
"""

from django.test import TestCase

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode

from .models import PrerequisiteEdge
from .services.edge_derivation import derive_edges, learning_objects_for_material


class Fixture(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.topic = OutlineNode.objects.create(
            course=self.course, title="States of Matter", order=0, depth=0
        )
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic,
            title="States of Matter", status="completed",
        )

    def _object(self, order, title, content, section_title=""):
        return LearningObject.objects.create(
            material=self.material, title=title, content=content,
            section_title=section_title, order=order,
        )

    def _edge(self, edges, prerequisite, dependent):
        for edge in edges:
            if edge.prerequisite_id == prerequisite.id and edge.dependent_id == dependent.id:
                return edge
        return None


class ChunkContinuationTests(Fixture):
    """Structural, not inferred: two halves of one passage the chunker cut.

    It must not depend on evidence, or thin evidence would let a single
    explanation be scattered across the path.
    """

    def test_split_parts_are_linked_with_no_evidence_at_all(self):
        first = self._object(0, "Solid (Part 1 of 2)", "Zzz qqq.")
        second = self._object(1, "Solid (Part 2 of 2)", "Www vvv.")

        edge = self._edge(derive_edges([first, second], material=self.material), first, second)

        self.assertIsNotNone(edge)
        self.assertEqual(edge.signal, PrerequisiteEdge.Signal.CHUNK_CONTINUATION)
        self.assertEqual(edge.weight, 1.0)

    def test_separate_concepts_are_not_linked_as_continuations(self):
        gas = self._object(0, "Gas (Part 1 of 2)", "A gas has no definite shape.")
        other = self._object(1, "Plasma (Part 1 of 2)", "Plasma is an ionised state.")

        edges = derive_edges([gas, other], material=self.material)

        self.assertNotIn(
            PrerequisiteEdge.Signal.CHUNK_CONTINUATION, {e.signal for e in edges}
        )


class DerivationScopeTests(Fixture):
    def test_represented_objects_are_not_part_of_the_graph(self):
        matter = self._object(0, "Matter", "Matter is anything that has mass.")
        solid = self._object(1, "Solid", "A solid is a state of matter.")
        duplicate = self._object(2, "Solid", "A solid holds a fixed shape.")
        LearningObject.objects.filter(pk=duplicate.pk).update(represented_by=solid)

        objects = learning_objects_for_material(self.material.id)

        self.assertIn(matter, objects)
        self.assertIn(solid, objects)
        self.assertNotIn(duplicate, objects)


class WeakEvidenceCannotCreateEdgesTests(Fixture):
    """The single biggest change: a textual mention produced 88% of the old
    graph and can no longer produce any of it."""

    def test_a_bare_mention_creates_nothing(self):
        alpha = self._object(0, "Photosynthesis", "Photosynthesis feeds a plant.")
        beta = self._object(1, "Leaf", "The leaf is where photosynthesis happens.")

        edges = derive_edges([alpha, beta], material=self.material)

        self.assertIsNone(self._edge(edges, alpha, beta))

    def test_shared_distinctive_vocabulary_creates_nothing(self):
        first = self._object(0, "Notes", "The greenhouse absorbs infrared radiation warmly.")
        second = self._object(1, "Summary text", "Greenhouse infrared radiation matters here.")

        edges = derive_edges([first, second], material=self.material)

        self.assertIsNone(self._edge(edges, first, second))
