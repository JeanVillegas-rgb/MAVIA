from django.test import TestCase

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode

from .models import PrerequisiteEdge
from .services.edge_derivation import VOTE_THRESHOLD, derive_edges
from .services.edge_derivation import learning_objects_for_material


class VotingFixtureMixin:
    def build_material(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.topic = OutlineNode.objects.create(
            course=self.course, title="States of Matter", order=0, depth=0
        )
        self.material = LearningMaterial.objects.create(
            course=self.course, title="States of Matter", status="completed"
        )

    def _object(self, order, title, content, section_title=""):
        return LearningObject.objects.create(
            material=self.material,
            title=title,
            content=content,
            section_title=section_title,
            order=order,
        )

    def _edge(self, edges, prerequisite, dependent):
        for edge in edges:
            if edge.prerequisite_id == prerequisite.id and edge.dependent_id == dependent.id:
                return edge
        return None


class ReferenceAsymmetryTests(VotingFixtureMixin, TestCase):
    """C1: a reference only counts when it is not returned.

    This is the RefD principle -- passages about solids refer to matter, but
    the passage defining matter does not refer back. A mention in both
    directions says the two are related, not which one comes first.
    """

    def setUp(self):
        self.build_material()

    def test_one_way_mention_votes_for_an_edge(self):
        matter = self._object(0, "Matter", "Matter is anything that has mass.")
        solid = self._object(1, "Solid", "A solid is matter with a definite shape.")

        edges = derive_edges([matter, solid])

        edge = self._edge(edges, matter, solid)
        self.assertIsNotNone(edge)
        self.assertIn("body_reference", edge.evidence["voted_forward"])

    def test_mutual_mention_casts_no_reference_vote(self):
        # Each passage names the other's concept, so the reference tells us
        # nothing about direction.
        matter = self._object(
            0, "Matter", "Matter is anything that has mass, including every solid."
        )
        solid = self._object(1, "Solid", "A solid is matter with a definite shape.")

        edges = derive_edges([matter, solid])

        edge = self._edge(edges, matter, solid)
        if edge is not None:
            self.assertNotIn("body_reference", edge.evidence["voted_forward"])

    def test_no_edge_runs_against_document_order(self):
        matter = self._object(0, "Matter", "Matter is anything that has mass.")
        solid = self._object(1, "Solid", "A solid is matter with a definite shape.")

        edges = derive_edges([matter, solid])

        self.assertIsNone(self._edge(edges, solid, matter))


class VoteScoringTests(VotingFixtureMixin, TestCase):
    def setUp(self):
        self.build_material()

    def test_weight_stores_the_normalised_vote_score(self):
        matter = self._object(0, "Matter", "Matter is anything that has mass.")
        solid = self._object(1, "Solid", "A solid is matter with a definite shape.")

        edge = self._edge(derive_edges([matter, solid]), matter, solid)

        self.assertGreaterEqual(edge.weight, VOTE_THRESHOLD)
        self.assertLessEqual(edge.weight, 1.0)

    def test_evidence_records_the_score_and_the_threshold_in_force(self):
        matter = self._object(0, "Matter", "Matter is anything that has mass.")
        solid = self._object(1, "Solid", "A solid is matter with a definite shape.")

        edge = self._edge(derive_edges([matter, solid]), matter, solid)

        self.assertIn("score", edge.evidence)
        self.assertEqual(edge.evidence["threshold"], VOTE_THRESHOLD)
        self.assertIn("voted_forward", edge.evidence)
        self.assertIn("voted_backward", edge.evidence)

    def test_unrelated_passages_produce_no_edge(self):
        first = self._object(0, "Weather", "Rain falls from clouds in the sky.")
        second = self._object(1, "Music", "A drum makes a sound when it is struck.")

        edges = derive_edges([first, second])

        self.assertIsNone(self._edge(edges, first, second))

    def test_voted_edges_carry_the_voted_signal(self):
        matter = self._object(0, "Matter", "Matter is anything that has mass.")
        solid = self._object(1, "Solid", "A solid is matter with a definite shape.")

        edge = self._edge(derive_edges([matter, solid]), matter, solid)

        self.assertEqual(edge.signal, PrerequisiteEdge.Signal.VOTED)


class RemovedSignalTests(VotingFixtureMixin, TestCase):
    def setUp(self):
        self.build_material()

    def test_definition_scope_is_no_longer_a_signal(self):
        self.assertFalse(hasattr(PrerequisiteEdge.Signal, "DEFINITION_SCOPE"))

    def test_reference_and_cooccurrence_are_criteria_not_signals(self):
        for name in ("TITLE_REFERENCE", "SECTION_REFERENCE", "TERM_COOCCURRENCE"):
            self.assertFalse(hasattr(PrerequisiteEdge.Signal, name), msg=name)

    def test_definition_scope_survives_as_a_criterion(self):
        """It grounds later definitions, but now as one vote among four.

        The relation it approximates -- a general concept containing the
        specific ones -- is recognised in the literature as category
        containment. We have no knowledge base to detect it properly, so this
        local proxy earns one vote, not a weight of its own.
        """
        matter = self._object(0, "Matter", "Matter is anything that has mass.")
        solid = self._object(1, "Solid", "A solid has a definite shape.")

        edge = self._edge(derive_edges([matter, solid]), matter, solid)

        self.assertIsNotNone(edge)
        self.assertIn("definition_scope", edge.evidence["voted_forward"])
        self.assertEqual(edge.signal, PrerequisiteEdge.Signal.VOTED)

    def test_a_passage_that_merely_describes_does_not_ground_others(self):
        matter = self._object(0, "Matter", "Matter is anything that has mass.")
        behaviour = self._object(
            1, "Shape", "Solids keep their shape while liquids take the container's shape."
        )

        edges = derive_edges([matter, behaviour])

        for edge in edges:
            self.assertNotIn("definition_scope", edge.evidence.get("voted_forward", {}))


class ChunkContinuationTests(VotingFixtureMixin, TestCase):
    """Kept deliberately: this is document order applied to a chunker artifact,
    not a discovered prerequisite, so it must not depend on the vote."""

    def setUp(self):
        self.build_material()

    def test_split_parts_are_linked_even_with_no_votes(self):
        first = self._object(0, "Solid (Part 1 of 2)", "Zzz qqq.")
        second = self._object(1, "Solid (Part 2 of 2)", "Www vvv.")

        edges = derive_edges([first, second])

        edge = self._edge(edges, first, second)
        self.assertIsNotNone(edge)
        self.assertEqual(edge.signal, PrerequisiteEdge.Signal.CHUNK_CONTINUATION)
        self.assertEqual(edge.weight, 1.0)


class DerivationScopeTests(VotingFixtureMixin, TestCase):
    def setUp(self):
        self.build_material()

    def test_represented_objects_are_not_part_of_the_graph(self):
        matter = self._object(0, "Matter", "Matter is anything that has mass.")
        solid = self._object(1, "Solid", "A solid is matter with a definite shape.")
        duplicate = self._object(2, "Solid", "A solid holds a fixed shape.")
        LearningObject.objects.filter(pk=duplicate.pk).update(represented_by=solid)

        objects = learning_objects_for_material(self.material.id)

        self.assertIn(matter, objects)
        self.assertIn(solid, objects)
        self.assertNotIn(duplicate, objects)
