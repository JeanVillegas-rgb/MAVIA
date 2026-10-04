"""The teacher's corrections to an automatic bundle.

Bundles are placed without asking, so every correction is a plain button: move
an object out, move it to another concept, or change its place in the bundle.
"""

import os
from unittest.mock import patch

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
from .services.unit_matching import refresh_heading_unit_suggestions
from .test_semantic_grouping import FakeRuntime
from .test_unit_matching import SEMANTIC_ENV
from .tests import authenticated_api_client


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


class MoveOutTests(BundleControlFixture):
    def test_move_out_gives_the_object_its_own_concept(self):
        client = authenticated_api_client()

        response = client.post(self._url(f"learning-objects/{self.diagram.id}/move-out/"), format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.diagram.refresh_from_db()
        self.assertNotEqual(self.diagram.group_id, self.solid.group_id)
        self.assertEqual(self.diagram.group.learning_objects.count(), 1)


class MoveToTests(BundleControlFixture):
    def test_move_to_puts_the_object_in_the_named_concept(self):
        other = LearningObjectGroup.objects.create(outline_node=self.topic, label="Liquid")
        client = authenticated_api_client()

        response = client.post(
            self._url(f"learning-objects/{self.diagram.id}/move-to/"),
            {"group_id": other.id}, format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.diagram.refresh_from_db()
        self.assertEqual(self.diagram.group_id, other.id)

    def test_move_to_rejects_a_concept_from_another_topic(self):
        elsewhere = OutlineNode.objects.create(course=self.course, title="Other", order=1)
        foreign = LearningObjectGroup.objects.create(outline_node=elsewhere, label="Nope")
        client = authenticated_api_client()

        response = client.post(
            self._url(f"learning-objects/{self.diagram.id}/move-to/"),
            {"group_id": foreign.id}, format="json",
        )

        self.assertEqual(response.status_code, 400)


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
        """The earliest upload is Normal; a role a teacher set is reported as it is."""
        set_bundle_role(self.group, self.second.id, "ELABORATED")
        client = authenticated_api_client()

        response = client.get(self._url("learning-resources/"))

        roles = {row["material"]: row["role"] for row in self._group_row(response)["bundles"]}
        self.assertEqual(roles[self.first.id], "NORMAL")
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


class LeavingAGroupTests(BundleControlFixture):
    """What a move does to the members it leaves behind."""

    def setUp(self):
        super().setUp()
        # The concept is taught through A's object: B's two are suppressed
        # duplicates until the Normal leaves.
        LearningObject.objects.filter(
            pk__in=[self.solids.id, self.diagram.id]
        ).update(represented_by=self.solid)

    def _published_object_ids(self):
        """What the published lesson and its audio actually read."""
        return set(
            LearningObject.objects.filter(
                material__outline_node=self.topic,
                represented_by__isnull=True,
            ).values_list("id", flat=True)
        )

    def test_moving_the_normal_out_releases_the_objects_left_behind(self):
        client = authenticated_api_client()

        response = client.post(self._url(f"learning-objects/{self.solid.id}/move-out/"), format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.solids.refresh_from_db()
        self.diagram.refresh_from_db()
        self.assertIsNone(self.solids.represented_by_id)
        self.assertIsNone(self.diagram.represented_by_id)
        self.assertTrue({self.solids.id, self.diagram.id}.issubset(self._published_object_ids()))

    def test_moving_the_normal_to_another_concept_releases_them_too(self):
        other = LearningObjectGroup.objects.create(outline_node=self.topic, label="Liquid")
        client = authenticated_api_client()

        response = client.post(
            self._url(f"learning-objects/{self.solid.id}/move-to/"),
            {"group_id": other.id}, format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.solids.refresh_from_db()
        self.diagram.refresh_from_db()
        self.assertIsNone(self.solids.represented_by_id)
        self.assertIsNone(self.diagram.represented_by_id)
        self.assertTrue({self.solids.id, self.diagram.id}.issubset(self._published_object_ids()))


class MoveOutIsRememberedTests(BundleControlFixture):
    def setUp(self):
        super().setUp()
        # Named after the bundle that stays, so the concept's label still
        # looks automatic after the move. A locked label would block automatic
        # placement on its own and hide what this test is about.
        self.group.label = "Solids"
        self.group.save(update_fields=["label"])

    def test_the_automatic_pass_does_not_put_the_object_straight_back(self):
        """A teacher breaking a bundle up is a decision the next pass must obey."""
        client = authenticated_api_client()
        response = client.post(self._url(f"learning-objects/{self.solid.id}/move-out/"), format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.solid.refresh_from_db()
        moved_to = self.solid.group_id

        runtime = FakeRuntime()
        runtime.pair_scores = lambda pairs: [0.95 for _ in pairs]
        with patch.dict(os.environ, SEMANTIC_ENV):
            counts = refresh_heading_unit_suggestions(self.topic, runtime_instance=runtime)

        self.solid.refresh_from_db()
        self.solids.refresh_from_db()
        self.assertEqual(self.solid.group_id, moved_to)
        self.assertNotEqual(self.solid.group_id, self.solids.group_id)
        self.assertEqual(counts["placed"], 0)


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

    def test_every_bundle_control_refuses_an_unconfirmed_file(self):
        other = LearningObjectGroup.objects.create(outline_node=self.topic, label="Liquid")
        client = authenticated_api_client()

        for suffix, body in (
            (f"learning-objects/{self.diagram.id}/move-out/", {}),
            (f"learning-objects/{self.diagram.id}/move-to/", {"group_id": other.id}),
            (f"learning-objects/{self.diagram.id}/reorder/", {"direction": "up"}),
        ):
            with self.subTest(suffix=suffix):
                response = client.post(self._url(suffix), body, format="json")
                self.assertEqual(response.status_code, 400, suffix)
        self.diagram.refresh_from_db()
        self.assertEqual(self.diagram.group_id, self.group.id)
