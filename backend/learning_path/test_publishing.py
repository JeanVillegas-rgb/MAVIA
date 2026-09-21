"""Saving a topic's learning path at publish.

Pins the three rules agreed for it: teacher decisions are never overwritten,
prerequisite links win over document order, and each save replaces the last.
"""

import json
import os
import tempfile
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from lessons.models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode

from .models import ConceptPrerequisite, LearningPathStep
from .services import publishing
from .services.concept_units import concepts_for_topic
from .services.publishing import order_with_links


def decision(prerequisite, dependent, verdict, cross_section=False):
    return {
        "prerequisite": prerequisite,
        "dependent": dependent,
        "verdict": verdict,
        "votes": {"temporal_order": 1, "semantic_reference": 1, "inbound_outbound": 1},
        "cross_section": cross_section,
    }


class PublishingFixture(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="Solid, Liquid and Gas", order=0, depth=0)
        self.material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="Lesson",
            generated_json={"learning_objects_confirmed": True},
        )
        LearningMaterial.objects.filter(pk=self.material.pk).update(created_at=timezone.now() - timedelta(days=1))
        self.groups = {}
        for order, title in enumerate(["Matter", "Solid", "Solid diagram", "Liquid"]):
            group = LearningObjectGroup.objects.create(outline_node=self.topic, label=title)
            LearningObject.objects.create(
                material=self.material, group=group, title=title,
                content=f"About {title}.", order=order,
            )
            self.groups[title] = group

    def _concepts(self):
        return {concept.title: concept for concept in concepts_for_topic(self.topic)}

    def _derive(self, *rows):
        """Replace the criteria with a fixed outcome for one refresh."""
        concepts = self._concepts()
        built = [decision(concepts[a], concepts[b], verdict, cross) for a, b, verdict, cross in rows]
        return patch.object(publishing.criteria, "decide_pairs", return_value=built)

    def _status(self, a, b):
        row = ConceptPrerequisite.objects.filter(
            prerequisite=self.groups[a], dependent=self.groups[b],
        ).first()
        return row.status if row else None

    def _saved_titles(self):
        return [
            step.concept.label
            for step in LearningPathStep.objects.filter(outline_node=self.topic).select_related("concept")
        ]


class RefreshPrerequisiteTests(PublishingFixture):
    def test_derived_links_are_stored_with_their_status(self):
        with self._derive(("Matter", "Solid", "accepted", False), ("Solid", "Liquid", "pending", True)):
            counts = publishing.refresh_prerequisites(self.topic)

        self.assertEqual(self._status("Matter", "Solid"), "accepted")
        self.assertEqual(self._status("Solid", "Liquid"), "pending")
        self.assertTrue(ConceptPrerequisite.objects.get(prerequisite=self.groups["Solid"]).cross_section)
        self.assertEqual(counts, {"accepted": 1, "pending": 1, "teacher_decided": 0})

    def test_a_derived_link_the_criteria_drop_is_removed(self):
        with self._derive(("Matter", "Solid", "accepted", False)):
            publishing.refresh_prerequisites(self.topic)
        with self._derive():
            publishing.refresh_prerequisites(self.topic)

        self.assertIsNone(self._status("Matter", "Solid"))

    def test_a_teacher_rejection_is_never_overwritten_or_proposed_again(self):
        ConceptPrerequisite.objects.create(
            outline_node=self.topic, prerequisite=self.groups["Solid"], dependent=self.groups["Liquid"],
            status="rejected", source="teacher",
        )

        with self._derive(("Solid", "Liquid", "accepted", False)):
            publishing.refresh_prerequisites(self.topic)

        self.assertEqual(self._status("Solid", "Liquid"), "rejected")

    def test_a_teacher_approval_survives_the_criteria_forgetting_it(self):
        ConceptPrerequisite.objects.create(
            outline_node=self.topic, prerequisite=self.groups["Matter"], dependent=self.groups["Liquid"],
            status="approved", source="teacher",
        )

        with self._derive():
            publishing.refresh_prerequisites(self.topic)

        self.assertEqual(self._status("Matter", "Liquid"), "approved")


class OrderTests(PublishingFixture):
    def test_without_links_the_document_order_is_kept(self):
        with self._derive():
            publishing.publish_learning_path(self.topic)

        self.assertEqual(self._saved_titles(), ["Matter", "Solid", "Solid diagram", "Liquid"])

    def test_an_approved_link_against_the_order_moves_the_step(self):
        """The case from the hand-check: a teacher said the Solid diagram must come
        before Solid, so the diagram moves up and nothing else changes."""
        ConceptPrerequisite.objects.create(
            outline_node=self.topic, prerequisite=self.groups["Solid diagram"], dependent=self.groups["Solid"],
            status="approved", source="teacher",
        )

        with self._derive():
            summary = publishing.publish_learning_path(self.topic)

        self.assertEqual(self._saved_titles(), ["Matter", "Solid diagram", "Solid", "Liquid"])
        self.assertEqual(summary["moved"], 2)

    def test_pending_links_do_not_shape_the_order(self):
        with self._derive(("Liquid", "Matter", "pending", False)):
            publishing.publish_learning_path(self.topic)

        self.assertEqual(self._saved_titles()[0], "Matter")

    def test_depth_counts_the_links_leading_to_a_step(self):
        with self._derive(("Matter", "Solid", "accepted", False), ("Solid", "Liquid", "accepted", False)):
            publishing.publish_learning_path(self.topic)

        depths = {
            step.concept.label: step.depth
            for step in LearningPathStep.objects.filter(outline_node=self.topic).select_related("concept")
        }
        self.assertEqual(depths, {"Matter": 0, "Solid": 1, "Solid diagram": 0, "Liquid": 2})

    def test_contradicting_links_are_reported_not_hidden(self):
        concepts = list(concepts_for_topic(self.topic))
        by_title = {concept.title: concept for concept in concepts}
        loop = [(by_title["Solid"].id, by_title["Liquid"].id), (by_title["Liquid"].id, by_title["Solid"].id)]

        ordered, _, ignored = publishing.order_with_links(concepts, loop)

        self.assertEqual(len(ordered), 4)
        self.assertEqual(len(ignored), 1)

    def test_saving_again_replaces_the_previous_path(self):
        with self._derive():
            publishing.publish_learning_path(self.topic)
            publishing.publish_learning_path(self.topic)

        self.assertEqual(LearningPathStep.objects.filter(outline_node=self.topic).count(), 4)


class ImportHandCheckTests(PublishingFixture):
    def _write(self, edges):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"topic": {"outline_node_id": self.topic.id}, "edges": edges}, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def _edge(self, a, b, answer):
        return {
            "prerequisite_group_id": self.groups[a].id, "prerequisite_title": a,
            "dependent_group_id": self.groups[b].id, "dependent_title": b,
            "answer": answer,
        }

    def test_yes_approves_no_rejects_unsure_is_left_alone(self):
        path = self._write([
            self._edge("Matter", "Solid", "yes"),
            self._edge("Solid", "Liquid", "no"),
            self._edge("Solid diagram", "Liquid", "unsure"),
        ])

        call_command("import_hand_check", path, stdout=StringIO())

        self.assertEqual(self._status("Matter", "Solid"), "approved")
        self.assertEqual(self._status("Solid", "Liquid"), "rejected")
        self.assertIsNone(self._status("Solid diagram", "Liquid"))

    def test_an_answer_about_a_concept_that_no_longer_exists_is_skipped(self):
        edge = self._edge("Matter", "Solid", "yes")
        edge["dependent_group_id"] = 999999
        out = StringIO()

        call_command("import_hand_check", self._write([edge]), stdout=out)

        self.assertEqual(ConceptPrerequisite.objects.count(), 0)
        self.assertIn("no longer exists", out.getvalue())


class StructuralOrderTests(TestCase):
    def test_examples_come_last_even_when_the_document_puts_them_first(self):
        concepts = [
            SimpleNamespace(id=1, title="Everyday Examples"),
            SimpleNamespace(id=2, title="Solid"),
            SimpleNamespace(id=3, title="Gas"),
        ]

        ordered, _, _ = order_with_links(concepts, [])

        self.assertEqual([concept.id for concept in ordered], [2, 3, 1])
