"""Bundle payload and ordering tests."""

from django.test import TestCase

from course.models import LessonVariant
from course.version_assignment import set_bundle_role

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)
from .test_utils import authenticated_api_client


class BundleControlFixture(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.first = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A", generated_json=dict(confirmed))
        self.second = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="B", generated_json=dict(confirmed))
        self.group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solid")
        self.solid = LearningObject.objects.create(
            material=self.first, group=self.group, title="Solid", order=0,
            content="A solid keeps its shape.")
        self.solids = LearningObject.objects.create(
            material=self.second, group=self.group, title="Solids", order=0,
            section_title="Solids", content="Packed tightly.")
        self.diagram = LearningObject.objects.create(
            material=self.second, group=self.group, title="Diagram description", order=1,
            section_title="Solids", content="Particles drawn in a grid.")

    def _url(self, suffix):
        return f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}/{suffix}"


class ReorderTests(BundleControlFixture):
    def test_reorder_swaps_with_the_neighbour_in_the_same_bundle(self):
        client = authenticated_api_client()

        response = client.post(
            self._url(f"learning-objects/{self.diagram.id}/reorder/"),
            {"direction": "up"}, format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.solids.refresh_from_db()
        self.diagram.refresh_from_db()
        self.assertLess(self.diagram.order, self.solids.order)

    def test_reorder_at_the_edge_is_a_no_op(self):
        client = authenticated_api_client()

        response = client.post(
            self._url(f"learning-objects/{self.solids.id}/reorder/"),
            {"direction": "up"}, format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.solids.refresh_from_db()
        self.assertEqual(self.solids.order, 0)


class PayloadTests(BundleControlFixture):
    def _group_row(self, response):
        return next(
            row for row in response.data["learning_object_groups"] if row["id"] == self.solid.group_id
        )

    def test_the_payload_lists_bundles_per_pdf_in_order(self):
        client = authenticated_api_client()

        response = client.get(self._url("learning-resources/"))

        group = self._group_row(response)
        bundles = {row["material"]: [item["id"] for item in row["learning_objects"]] for row in group["bundles"]}
        self.assertEqual(bundles[self.first.id], [self.solid.id])
        self.assertEqual(bundles[self.second.id], [self.solids.id, self.diagram.id])

    def test_each_bundle_reports_the_role_it_plays(self):
        """The earliest upload is Standard; a role a teacher set is reported as it is."""
        set_bundle_role(self.group, self.second.id, "ELABORATED")
        client = authenticated_api_client()

        response = client.get(self._url("learning-resources/"))

        roles = {row["material"]: row["role"] for row in self._group_row(response)["bundles"]}
        self.assertEqual(roles[self.first.id], "STANDARD")
        self.assertEqual(roles[self.second.id], "ELABORATED")

    def test_a_pdf_supplied_simplified_fills_its_slot(self):
        """A version a PDF supplies writes no ``LessonVariant`` row.

        Building the slots from the variant table alone therefore showed the
        concept an empty Simplified and refused to call it complete.
        """
        set_bundle_role(self.group, self.second.id, "SIMPLIFIED")
        LessonVariant.objects.create(
            learning_object=self.solid,
            variant="ELABORATED",
            narration="A solid holds its own shape because its particles barely move.",
            origin=LessonVariant.Origin.GENERATED,
        )
        client = authenticated_api_client()

        response = client.get(self._url("learning-resources/"))

        versions = self._group_row(response)["versions"]
        simplified = versions["slots"]["simplified"]
        self.assertIn("Packed tightly.", simplified["text"])
        self.assertIn("Particles drawn in a grid.", simplified["text"])
        self.assertEqual(simplified["origin"], LessonVariant.Origin.SOURCE_PDF)
        self.assertEqual(simplified["source_learning_object_id"], self.solids.id)
        self.assertTrue(versions["complete"])


class TiedOrderTests(BundleControlFixture):
    def setUp(self):
        super().setUp()
        # Two objects of one PDF extracted with the same `order`: their
        # sequence was decided by id alone, so a plain swap changes nothing.
        LearningObject.objects.filter(pk=self.diagram.id).update(order=0)
        self.diagram.refresh_from_db()

    def test_a_tie_is_renumbered_rather_than_swapped(self):
        client = authenticated_api_client()

        response = client.post(
            self._url(f"learning-objects/{self.diagram.id}/reorder/"),
            {"direction": "up"}, format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.solids.refresh_from_db()
        self.diagram.refresh_from_db()
        self.assertGreaterEqual(self.diagram.order, 0)
        self.assertGreaterEqual(self.solids.order, 0)
        self.assertNotEqual(self.diagram.order, self.solids.order)
        self.assertEqual([self.diagram.order, self.solids.order], [0, 1])

    def test_pressing_up_repeatedly_never_walks_the_order_negative(self):
        client = authenticated_api_client()

        for _ in range(3):
            response = client.post(
                self._url(f"learning-objects/{self.diagram.id}/reorder/"),
                {"direction": "up"}, format="json",
            )
            self.assertEqual(response.status_code, 200, response.data)

        orders = list(
            LearningObject.objects.filter(material=self.second).values_list("order", flat=True)
        )
        self.assertTrue(all(value >= 0 for value in orders), orders)

    def test_a_bundle_keeps_the_places_it_holds_in_its_own_pdf(self):
        """`order` is the whole PDF's reading order, not the concept's.

        Renumbering a bundle 0..n-1 would move it in front of the same file's
        other concepts and rewrite the lesson's audio order.
        """
        elsewhere = LearningObjectGroup.objects.create(outline_node=self.topic, label="Liquid")
        early = LearningObject.objects.create(
            material=self.second, group=elsewhere, title="Liquids", order=0,
            section_title="Liquids", content="Liquids flow.")
        LearningObject.objects.filter(pk=self.solids.id).update(order=1)
        LearningObject.objects.filter(pk=self.diagram.id).update(order=2)
        client = authenticated_api_client()

        response = client.post(
            self._url(f"learning-objects/{self.diagram.id}/reorder/"),
            {"direction": "up"}, format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        early.refresh_from_db()
        self.solids.refresh_from_db()
        self.diagram.refresh_from_db()
        self.assertEqual(early.order, 0)
        self.assertEqual([self.diagram.order, self.solids.order], [1, 2])


class UnconfirmedMaterialTests(BundleControlFixture):
    """Connections are reviewed only after the file's objects are confirmed."""

    def setUp(self):
        super().setUp()
        self.second.generated_json = {"learning_objects_confirmed": False}
        self.second.save(update_fields=["generated_json"])

    def test_reorder_refuses_an_unconfirmed_file(self):
        client = authenticated_api_client()

        suffix = f"learning-objects/{self.diagram.id}/reorder/"
        response = client.post(self._url(suffix), {"direction": "up"}, format="json")

        self.assertEqual(response.status_code, 400, suffix)
        self.diagram.refresh_from_db()
        self.assertEqual(self.diagram.group_id, self.group.id)
