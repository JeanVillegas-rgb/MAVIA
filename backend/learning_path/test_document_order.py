from django.test import TestCase

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode

from .services.path_builder import build_learning_path


class DocumentOrderTests(TestCase):
    """The author's sequence teaches; the graph supplies dependencies.

    Every derived edge is forced to run forward through the material, so
    document order is always a valid topological order of the graph. Teaching
    in it therefore cannot violate a prerequisite -- and it stops unvalidated
    criteria overriding a sequence a curriculum author chose deliberately.
    """

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        # Real materials always sit under a confirmed module/topic, and S3
        # reads that hierarchy. A fixture without one silently disables it.
        self.module = OutlineNode.objects.create(
            course=self.course, title="Properties of Matter", order=0, depth=0
        )
        self.topic = OutlineNode.objects.create(
            course=self.course, parent=self.module,
            title="Solid, Liquid and Gas", order=0, depth=1,
        )
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic,
            title="States of Matter", status="completed",
        )

    def _object(self, order, title, content, section_title=""):
        return LearningObject.objects.create(
            material=self.material,
            title=title,
            content=content,
            section_title=section_title,
            order=order,
        )

    def test_steps_follow_the_documents_own_order(self):
        self._object(0, "Matter", "Matter is anything that has mass.")
        self._object(1, "Solid", "A solid is matter with a definite shape.")
        self._object(2, "Shape", "Shape is the form a thing holds.")

        result = build_learning_path(self.material.id)

        self.assertEqual(
            [step["title"] for step in result["steps"]], ["Matter", "Solid", "Shape"]
        )
        self.assertEqual([step["position"] for step in result["steps"]], [1, 2, 3])

    def test_an_unconnected_step_keeps_its_place(self):
        """The flaw this replaces: a step with no evidence used to float to the
        front, because "nothing found" was indistinguishable from "needs
        nothing"."""
        self._object(0, "Matter", "Matter is anything that has mass.")
        self._object(1, "Solid", "A solid is matter with a definite shape.")
        # Deliberately not phrased as a definition and sharing no vocabulary,
        # so no criterion can reach it.
        floater = self._object(2, "Zebra", "Stripes cover them from nose to tail.")

        result = build_learning_path(self.material.id)

        last = result["steps"][-1]
        self.assertEqual(last["learning_object_id"], floater.id)
        self.assertEqual(last["prerequisite_count"], 0)

    def test_every_prerequisite_still_comes_before_its_dependent(self):
        self._object(0, "Matter", "Matter is anything that has mass.")
        self._object(1, "Solid", "A solid is matter with a definite shape.")
        self._object(2, "Ice", "Ice is a solid form of water.")

        result = build_learning_path(self.material.id)

        position_by_id = {s["learning_object_id"]: s["position"] for s in result["steps"]}
        for step in result["steps"]:
            for prerequisite_id in step["prerequisite_ids"]:
                self.assertLess(
                    position_by_id[prerequisite_id],
                    step["position"],
                    msg=f"{prerequisite_id} taught after {step['learning_object_id']}",
                )

    def test_dependency_structure_is_still_reported(self):
        matter = self._object(0, "Matter", "Matter is anything that has mass.")
        self._object(1, "Solid", "A solid is matter with a definite shape.")

        result = build_learning_path(self.material.id)

        solid = result["steps"][1]
        self.assertIn(matter.id, solid["prerequisite_ids"])
        self.assertGreaterEqual(solid["dag_depth"], 1)
        self.assertTrue(result["edges"])

    def test_diagnostics_report_that_document_order_is_used(self):
        self._object(0, "Matter", "Matter is anything that has mass.")
        self._object(1, "Solid", "A solid is matter with a definite shape.")

        diagnostics = build_learning_path(self.material.id)["diagnostics"]

        self.assertEqual(diagnostics["ordering"], "document")
        self.assertTrue(diagnostics["matches_source_order"])
        self.assertEqual(diagnostics["displaced_object_count"], 0)
