from django.test import TestCase

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode

from .models import PrerequisiteEdge
from .services import (
    GraphCycleError,
    build_adjacency,
    build_learning_path,
    derive_edges,
    kahn_topological_order,
    rebuild_edges_for_material,
)
from .services.topological_sort import mean_incoming_confidence


class KahnTopologicalSortTests(TestCase):
    """The ordering rule itself, exercised without touching the database."""

    def test_prerequisites_always_precede_dependents(self):
        nodes = [1, 2, 3, 4]
        edges = [(1, 2), (1, 3), (2, 4), (3, 4)]
        ordered, _ = kahn_topological_order(nodes, edges)
        self.assertEqual(ordered[0], 1)
        self.assertEqual(ordered[-1], 4)
        for prerequisite, dependent in edges:
            self.assertLess(ordered.index(prerequisite), ordered.index(dependent))

    def test_depth_is_longest_path_from_a_root(self):
        # 1 -> 2 -> 3 and 1 -> 3: node 3 sits at depth 2, not depth 1.
        _, depth = kahn_topological_order([1, 2, 3], [(1, 2), (2, 3), (1, 3)])
        self.assertEqual(depth, {1: 0, 2: 1, 3: 2})

    def test_lower_depth_wins_over_earlier_source_position(self):
        # Node 9 has no prerequisites but appears last in the source; node 2
        # depends on node 1. Depth ordering must hoist node 9 above node 2.
        nodes = [1, 2, 9]
        ordered, _ = kahn_topological_order(
            nodes, [(1, 2)], first_mention_rank={1: 0, 2: 1, 9: 99}
        )
        self.assertEqual(ordered, [1, 9, 2])

    def test_first_mention_breaks_ties_within_a_depth_layer(self):
        ordered, _ = kahn_topological_order(
            [10, 20, 30], [], first_mention_rank={10: 2, 20: 0, 30: 1}
        )
        self.assertEqual(ordered, [20, 30, 10])

    def test_fewer_prerequisites_breaks_ties_after_first_mention(self):
        # 3 and 4 both reach depth 1 with the same first-mention rank, but 4
        # has two prerequisites and 3 has one.
        nodes = [1, 2, 3, 4]
        edges = [(1, 3), (1, 4), (2, 4)]
        ranks = {1: 0, 2: 0, 3: 5, 4: 5}
        ordered, depth = kahn_topological_order(nodes, edges, first_mention_rank=ranks)
        self.assertEqual(depth[3], 1)
        self.assertEqual(depth[4], 1)
        self.assertLess(ordered.index(3), ordered.index(4))

    def test_ordering_is_stable_across_input_permutations(self):
        edges = [(1, 3), (2, 3), (3, 4)]
        ranks = {1: 0, 2: 1, 3: 2, 4: 3}
        first, _ = kahn_topological_order([1, 2, 3, 4], edges, first_mention_rank=ranks)
        second, _ = kahn_topological_order([4, 3, 2, 1], edges, first_mention_rank=ranks)
        self.assertEqual(first, second)

    def test_cycle_is_detected_rather_than_silently_truncated(self):
        with self.assertRaises(GraphCycleError) as caught:
            kahn_topological_order([1, 2, 3], [(1, 2), (2, 3), (3, 1)])
        self.assertEqual(caught.exception.unresolved_nodes, [1, 2, 3])

    def test_duplicate_edges_count_once_toward_in_degree(self):
        _, in_degree = build_adjacency([1, 2], [(1, 2), (1, 2)])
        self.assertEqual(in_degree[2], 1)


class LearningObjectFixtureMixin:
    def build_material(self):
        self.course = CourseGroup.objects.create(title="Science 5")
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


class EdgeDerivationTests(LearningObjectFixtureMixin, TestCase):
    def setUp(self):
        self.build_material()

    def test_naming_a_concept_creates_a_dependency_on_its_definition(self):
        matter = self._object(0, "Matter", "Matter is anything that has mass.")
        solid = self._object(1, "Solid", "A solid is matter with a definite shape.")
        edges = derive_edges([matter, solid])
        pairs = {(e.prerequisite_id, e.dependent_id, e.signal) for e in edges}
        self.assertIn(
            (matter.id, solid.id, PrerequisiteEdge.Signal.TITLE_REFERENCE), pairs
        )
        self.assertNotIn(
            (solid.id, matter.id, PrerequisiteEdge.Signal.TITLE_REFERENCE), pairs
        )

    def test_a_comparison_depends_on_every_concept_it_compares(self):
        """The case a parent_id tree cannot represent: multi-prerequisite fan-in."""
        solid = self._object(0, "Solid", "A solid keeps a definite shape.")
        liquid = self._object(1, "Liquid", "A liquid flows and fills its container.")
        gas = self._object(2, "Gas", "A gas spreads out to fill any space.")
        shape = self._object(
            3,
            "Shape",
            "Solids keep their shape; liquids and gases take the shape of the container.",
            section_title="Comparing the Three States",
        )
        edges = derive_edges([solid, liquid, gas, shape])
        prerequisites = {e.prerequisite_id for e in edges if e.dependent_id == shape.id}
        self.assertEqual(prerequisites, {solid.id, liquid.id, gas.id})

    def test_section_heading_mention_is_a_weaker_signal_than_content_mention(self):
        mixture = self._object(0, "Mixture", "A mixture combines two substances.")
        method = self._object(
            1,
            "Filtering",
            "Pour the sample through filter paper and collect what stays behind.",
            section_title="Separating a Mixture",
        )
        edges = derive_edges([mixture, method])
        signals = {e.signal for e in edges if e.dependent_id == method.id}
        self.assertEqual(signals, {PrerequisiteEdge.Signal.SECTION_REFERENCE})

    def test_split_parts_of_one_passage_stay_in_part_order(self):
        first = self._object(0, "Gas (Part 1 of 2)", "A gas has no definite shape.")
        second = self._object(1, "Gas (Part 2 of 2)", "A gas fills its container fully.")
        edges = derive_edges([first, second])
        pair_edges = [
            e
            for e in edges
            if {e.prerequisite_id, e.dependent_id} == {first.id, second.id}
        ]
        self.assertEqual(len(pair_edges), 1)
        self.assertEqual(pair_edges[0].prerequisite_id, first.id)
        self.assertEqual(
            pair_edges[0].signal, PrerequisiteEdge.Signal.CHUNK_CONTINUATION
        )

    def test_separate_concepts_are_not_linked_as_continuations(self):
        gas = self._object(0, "Gas (Part 1 of 2)", "A gas has no definite shape.")
        other = self._object(1, "Plasma (Part 1 of 2)", "Plasma is an ionised state.")
        edges = derive_edges([gas, other])
        self.assertNotIn(
            PrerequisiteEdge.Signal.CHUNK_CONTINUATION, {e.signal for e in edges}
        )

    def test_two_takes_on_one_concept_are_not_linked_to_each_other(self):
        """Unnumbered duplicates from different PDFs stay independent."""
        first = self._object(0, "Solid", "A solid has a definite shape.")
        second = self._object(1, "Solid", "Solids hold their own form.")
        edges = derive_edges([first, second])
        pair = {first.id, second.id}
        self.assertEqual(
            [e for e in edges if {e.prerequisite_id, e.dependent_id} == pair], []
        )

    def test_long_prose_titles_do_not_define_concepts(self):
        prose = self._object(
            0,
            "Matter is anything that has mass and occupies space",
            "Everything around us is made of matter.",
        )
        other = self._object(1, "Solid", "A solid holds its own space and shape.")
        edges = derive_edges([prose, other])
        self.assertNotIn(
            PrerequisiteEdge.Signal.TITLE_REFERENCE, {e.signal for e in edges}
        )

    def test_sibling_concepts_are_not_chained_by_shared_lesson_vocabulary(self):
        """Solid, Liquid and Gas are peers: none is a prerequisite of another.

        They share "definite", "shape", "volume", "particles" -- the background
        vocabulary of this whole lesson, which is not evidence of a dependency.
        """
        solid = self._object(
            0, "Solid", "A solid has a definite shape and definite volume with particles."
        )
        liquid = self._object(
            1, "Liquid", "A liquid has a definite volume and no definite shape with particles."
        )
        gas = self._object(
            2, "Gas", "A gas has no definite shape and no definite volume with particles."
        )
        self._object(3, "Volume", "Definite volume, shape and particles appear here too.")

        edges = derive_edges([solid, liquid, gas, self.material.learning_objects.last()])
        cooccurrence = [
            e for e in edges if e.signal == PrerequisiteEdge.Signal.TERM_COOCCURRENCE
        ]
        self.assertEqual(cooccurrence, [])
        self.assertEqual(
            [e for e in edges if (e.prerequisite_id, e.dependent_id) == (liquid.id, gas.id)],
            [],
        )

    def test_co_occurrence_still_fires_on_genuinely_distinctive_terms(self):
        """The fallback is narrowed, not disabled."""
        alpha = self._object(0, "Alpha", "Alpha is a thing.")
        notes = self._object(1, "Notes", "The greenhouse absorbs infrared radiation warmly.")
        summary = self._object(2, "Summary", "Greenhouse infrared radiation matters.")
        edges = derive_edges([alpha, notes, summary])
        cooccurrence = [
            e for e in edges if e.signal == PrerequisiteEdge.Signal.TERM_COOCCURRENCE
        ]
        self.assertEqual(len(cooccurrence), 1)
        self.assertEqual(cooccurrence[0].prerequisite_id, notes.id)
        self.assertEqual(cooccurrence[0].dependent_id, summary.id)

    def test_opening_definition_grounds_the_definitions_that_follow(self):
        """"A solid has a definite shape" never says "matter", so only the shape
        of the sentences can connect them."""
        matter = self._object(0, "Matter", "Matter is anything that has mass and occupies space.")
        solid = self._object(1, "Solid", "A solid has a definite shape and a definite volume.")
        liquid = self._object(2, "Liquid", "A liquid has a definite volume but no definite shape.")
        edges = derive_edges([matter, solid, liquid])
        scoped = {
            (e.prerequisite_id, e.dependent_id)
            for e in edges
            if e.signal == PrerequisiteEdge.Signal.DEFINITION_SCOPE
        }
        self.assertEqual(scoped, {(matter.id, solid.id), (matter.id, liquid.id)})

    def test_a_passage_that_merely_describes_is_not_treated_as_a_definition(self):
        matter = self._object(0, "Matter", "Matter is anything that has mass.")
        behaviour = self._object(
            1, "Shape", "Solids keep their shape while liquids take the container's shape."
        )
        edges = derive_edges([matter, behaviour])
        self.assertNotIn(
            PrerequisiteEdge.Signal.DEFINITION_SCOPE, {e.signal for e in edges}
        )

    def test_derived_edges_are_persisted_and_replaced_on_rebuild(self):
        self._object(0, "Matter", "Matter is anything that has mass.")
        self._object(1, "Solid", "A solid is matter with a definite shape.")
        first = rebuild_edges_for_material(self.material.id)
        self.assertGreater(len(first), 0)
        self.assertEqual(
            PrerequisiteEdge.objects.filter(dependent__material=self.material).count(),
            len(first),
        )
        rebuild_edges_for_material(self.material.id)
        self.assertEqual(
            PrerequisiteEdge.objects.filter(dependent__material=self.material).count(),
            len(first),
        )


class LearningPathTests(LearningObjectFixtureMixin, TestCase):
    def setUp(self):
        self.build_material()

    def test_path_respects_every_derived_prerequisite(self):
        self._object(0, "Matter", "Matter is anything that has mass and takes up space.")
        self._object(1, "Solid", "A solid is matter with a definite shape and volume.")
        self._object(2, "Liquid", "A liquid is matter that flows and has definite volume.")
        self._object(
            3,
            "Flow",
            "Liquids can flow but solids normally do not flow at all.",
            section_title="Comparing the States",
        )
        result = build_learning_path(self.material.id)
        order = {node_id: index for index, node_id in enumerate(result["learning_path"])}
        for step in result["steps"]:
            for prerequisite_id in step["prerequisite_ids"]:
                self.assertLess(
                    order[prerequisite_id], order[step["learning_object_id"]]
                )

    def test_path_is_not_a_copy_of_the_source_document_order(self):
        # "Weather" arrives last in the PDF but depends on nothing, so the depth
        # tie-breaker must hoist it above the comparison passage.
        self._object(0, "Matter", "Matter is anything that has mass.")
        self._object(1, "Solid", "A solid is matter with a definite shape.")
        self._object(2, "Liquid", "A liquid is matter that flows freely.")
        self._object(
            3,
            "Shape",
            "Solids keep their shape while liquids take the shape of the container.",
            section_title="Comparing the States",
        )
        self._object(4, "Weather", "Rain, wind and sunshine change from day to day.")

        result = build_learning_path(self.material.id)
        self.assertFalse(result["diagnostics"]["matches_source_order"])
        self.assertGreater(result["diagnostics"]["displaced_object_count"], 0)

        titles = [step["title"] for step in result["steps"]]
        self.assertLess(titles.index("Weather"), titles.index("Shape"))

    def test_foundational_definition_is_taught_before_its_subtypes(self):
        self._object(0, "Matter", "Matter is anything that has mass and occupies space.")
        solid = self._object(1, "Solid", "A solid has a definite shape and a definite volume.")
        liquid = self._object(2, "Liquid", "A liquid has a definite volume but no definite shape.")
        gas = self._object(3, "Gas", "A gas has no definite shape and no definite volume.")

        result = build_learning_path(self.material.id)
        depth_by_id = {s["learning_object_id"]: s["dag_depth"] for s in result["steps"]}
        self.assertEqual(result["steps"][0]["title"], "Matter")
        # The three states are peers: same layer, no chain between them.
        self.assertEqual(
            {depth_by_id[solid.id], depth_by_id[liquid.id], depth_by_id[gas.id]}, {1}
        )

    def test_path_is_a_permutation_of_the_material_and_is_reproducible(self):
        for index in range(6):
            self._object(index, f"Concept {index}", f"Body text about concept {index}.")
        first = build_learning_path(self.material.id)
        second = build_learning_path(self.material.id)
        self.assertEqual(first["learning_path"], second["learning_path"])
        self.assertCountEqual(
            first["learning_path"],
            list(
                LearningObject.objects.filter(material=self.material).values_list(
                    "id", flat=True
                )
            ),
        )

    def test_isolated_objects_still_appear_in_the_path(self):
        alone = self._object(0, "Unrelated", "Nothing here connects to anything else.")
        result = build_learning_path(self.material.id)
        self.assertEqual(result["learning_path"], [alone.id])
        self.assertEqual(result["diagnostics"]["edge_count"], 0)

    def test_endpoint_is_reachable_without_signing_in(self):
        """Matches the rest of the teacher authoring flow, which has no auth."""
        response = self.client.get(f"/api/learning-path/materials/{self.material.id}/")
        self.assertEqual(response.status_code, 200)

    def test_endpoint_returns_the_path(self):
        self._object(0, "Matter", "Matter is anything that has mass.")
        self._object(1, "Solid", "A solid is matter with a definite shape.")
        response = self.client.get(f"/api/learning-path/materials/{self.material.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["learning_path"]), 2)

    def test_endpoint_404s_for_an_unknown_material(self):
        response = self.client.get("/api/learning-path/materials/999999/")
        self.assertEqual(response.status_code, 404)


class EdgeConfidenceOrderingTests(TestCase):
    """Edge weight is a real input to the sort, not just stored metadata."""

    def test_better_evidenced_node_is_taught_first_within_a_layer(self):
        nodes, edges = [0, 1, 2], [(0, 1), (0, 2)]
        ranks = {0: 0, 1: 1, 2: 2}

        weak_first, _ = kahn_topological_order(
            nodes, edges, first_mention_rank=ranks,
            edge_weights={(0, 1): 0.4, (0, 2): 1.0},
        )
        self.assertEqual(weak_first, [0, 2, 1])

        strong_first, _ = kahn_topological_order(
            nodes, edges, first_mention_rank=ranks,
            edge_weights={(0, 1): 1.0, (0, 2): 0.4},
        )
        self.assertEqual(strong_first, [0, 1, 2])

    def test_confidence_never_overrides_dependency_order(self):
        """A weak edge is still a hard constraint, not a preference."""
        ordered, _ = kahn_topological_order(
            [1, 2], [(1, 2)], edge_weights={(1, 2): 0.1}
        )
        self.assertEqual(ordered, [1, 2])

    def test_roots_count_as_fully_supported(self):
        confidence = mean_incoming_confidence([1, 2], [(1, 2)], {(1, 2): 0.4})
        self.assertEqual(confidence[1], 1.0)
        self.assertAlmostEqual(confidence[2], 0.4)

    def test_omitting_weights_leaves_the_order_unchanged(self):
        nodes, edges = [0, 1, 2], [(0, 1), (0, 2)]
        ranks = {0: 0, 1: 1, 2: 2}
        with_defaults, _ = kahn_topological_order(nodes, edges, first_mention_rank=ranks)
        self.assertEqual(with_defaults, [0, 1, 2])


class TeacherEditedPathTests(LearningObjectFixtureMixin, TestCase):
    def setUp(self):
        self.build_material()
        self.first = self._object(0, "Alpha", "Alpha is a thing.")
        self.second = self._object(1, "Beta", "Beta is another thing entirely.")
        build_learning_path(self.material.id, rebuild=True)

    def test_teacher_can_add_a_prerequisite(self):
        response = self.client.post(
            "/api/learning-path/edges/",
            {"prerequisite_id": self.first.id, "dependent_id": self.second.id},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        edge = PrerequisiteEdge.objects.get(
            prerequisite=self.first, dependent=self.second,
            signal=PrerequisiteEdge.Signal.TEACHER_AUTHORED,
        )
        self.assertEqual(edge.source, PrerequisiteEdge.Source.TEACHER)

    def test_a_prerequisite_that_would_loop_is_refused_and_not_saved(self):
        self.client.post(
            "/api/learning-path/edges/",
            {"prerequisite_id": self.first.id, "dependent_id": self.second.id},
            content_type="application/json",
        )
        response = self.client.post(
            "/api/learning-path/edges/",
            {"prerequisite_id": self.second.id, "dependent_id": self.first.id},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(
            PrerequisiteEdge.objects.filter(
                prerequisite=self.second, dependent=self.first
            ).exists()
        )

    def test_teacher_edges_survive_re_derivation(self):
        self.client.post(
            "/api/learning-path/edges/",
            {"prerequisite_id": self.first.id, "dependent_id": self.second.id},
            content_type="application/json",
        )
        rebuild_edges_for_material(self.material.id)
        self.assertTrue(
            PrerequisiteEdge.objects.filter(
                prerequisite=self.first, dependent=self.second,
                source=PrerequisiteEdge.Source.TEACHER,
            ).exists()
        )

    def test_teacher_can_delete_a_prerequisite(self):
        self.client.post(
            "/api/learning-path/edges/",
            {"prerequisite_id": self.first.id, "dependent_id": self.second.id},
            content_type="application/json",
        )
        edge = PrerequisiteEdge.objects.get(source=PrerequisiteEdge.Source.TEACHER)
        response = self.client.delete(f"/api/learning-path/edges/{edge.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PrerequisiteEdge.objects.filter(id=edge.id).exists())

    def test_self_prerequisite_is_refused(self):
        response = self.client.post(
            "/api/learning-path/edges/",
            {"prerequisite_id": self.first.id, "dependent_id": self.first.id},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_topic_endpoint_returns_a_path_per_material(self):
        self.material.outline_node = self.topic
        self.material.save(update_fields=["outline_node"])
        response = self.client.get(f"/api/learning-path/topics/{self.topic.id}/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["topic"]["id"], self.topic.id)
        self.assertEqual(len(body["paths"]), 1)
        self.assertEqual(body["paths"][0]["material_id"], self.material.id)
