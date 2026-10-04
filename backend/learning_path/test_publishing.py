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
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from lessons.models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode

from .models import ConceptPrerequisite, LearningPathStep
from .services import publishing
from .services.concept_units import concepts_for_topic
from .services.path_builder import build_topic_path
from .services.publishing import break_cycles, order_with_links, path_link_confidence, redundant_links


def decision(prerequisite, dependent, verdict, cross_section=False):
    return {
        "prerequisite": prerequisite,
        "dependent": dependent,
        "verdict": verdict,
        "evidence": {"rule": "containment", "containment": {"heading": "matter"}},
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

    def test_a_link_the_criteria_still_produce_keeps_its_id(self):
        with self._derive(("Matter", "Solid", "accepted", False)):
            publishing.refresh_prerequisites(self.topic)
            first = ConceptPrerequisite.objects.get().id
            publishing.refresh_prerequisites(self.topic)

        self.assertEqual(ConceptPrerequisite.objects.get().id, first)

    def test_a_link_stored_by_a_concurrent_request_is_updated_not_duplicated(self):
        """Two screens opening at once (React runs effects twice in development)
        both derive; the one that read before the other committed must not
        insert the same pair again. Measured 2026-09-29: IntegrityError, HTTP 500."""
        ConceptPrerequisite.objects.create(
            outline_node=self.topic, prerequisite=self.groups["Matter"], dependent=self.groups["Solid"],
            status="pending", source="derived",
        )

        with self._derive(("Matter", "Solid", "accepted", False)), \
                patch.object(publishing, "_stored_links", return_value={}):
            publishing.refresh_prerequisites(self.topic)

        self.assertEqual(self._status("Matter", "Solid"), "accepted")
        self.assertEqual(ConceptPrerequisite.objects.count(), 1)

    def _stale_copy_of_a_teacher_decision(self, status):
        """A row a teacher decided after this derivation read it as derived.

        On PostgreSQL READ COMMITTED the read does not block a concurrent
        Accept, so the in-memory copy can be older than the stored row.
        """
        row = ConceptPrerequisite.objects.create(
            outline_node=self.topic, prerequisite=self.groups["Matter"], dependent=self.groups["Solid"],
            status=status, source="teacher",
        )
        stale = ConceptPrerequisite.objects.get(pk=row.pk)
        stale.status, stale.source = "pending", "derived"
        return {(row.prerequisite_id, row.dependent_id): stale}

    def test_a_concurrent_teacher_decision_is_not_overwritten(self):
        stale = self._stale_copy_of_a_teacher_decision("approved")

        with self._derive(("Matter", "Solid", "pending", False)), \
                patch.object(publishing, "_stored_links", return_value=stale):
            publishing.refresh_prerequisites(self.topic)

        self.assertEqual(self._status("Matter", "Solid"), "approved")

    def test_a_concurrent_teacher_decision_is_not_deleted(self):
        stale = self._stale_copy_of_a_teacher_decision("rejected")

        with self._derive(), patch.object(publishing, "_stored_links", return_value=stale):
            publishing.refresh_prerequisites(self.topic)

        self.assertEqual(self._status("Matter", "Solid"), "rejected")

    def test_a_changed_verdict_is_updated_in_place(self):
        with self._derive(("Matter", "Solid", "accepted", False)):
            publishing.refresh_prerequisites(self.topic)
        first = ConceptPrerequisite.objects.get().id

        with self._derive(("Matter", "Solid", "pending", True)):
            publishing.refresh_prerequisites(self.topic)

        row = ConceptPrerequisite.objects.get()
        self.assertEqual((row.id, row.status, row.cross_section), (first, "pending", True))


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


class CycleTests(SimpleTestCase):
    def test_the_least_confident_link_in_a_loop_is_dropped(self):
        kept, ignored = break_cycles({(1, 2), (2, 3), (3, 1)}, {(1, 2): 0.9, (2, 3): 0.4, (3, 1): 0.7})

        self.assertEqual(ignored, [(2, 3)])
        self.assertEqual(kept, {(1, 2), (3, 1)})

    def test_a_teacher_link_stays_while_a_derived_one_can_go(self):
        kept, ignored = break_cycles({(1, 2), (2, 1)}, {(2, 1): 0.99})

        self.assertEqual(ignored, [(2, 1)])

    def test_links_without_a_loop_are_all_kept(self):
        self.assertEqual(break_cycles({(1, 2), (2, 3)}), ({(1, 2), (2, 3)}, []))


class RedundantLinkTests(SimpleTestCase):
    def test_a_link_a_longer_chain_already_implies_is_redundant(self):
        self.assertEqual(redundant_links({(1, 2), (2, 3), (1, 3)}), {(1, 3)})

    def test_a_plain_chain_has_nothing_redundant(self):
        self.assertEqual(redundant_links({(1, 2), (2, 3)}), set())


class TieBreakTests(SimpleTestCase):
    def setUp(self):
        self.concepts = [SimpleNamespace(id=1, title="Solid"), SimpleNamespace(id=2, title="Changing"),
                         SimpleNamespace(id=3, title="Examples")]

    def test_a_concept_building_on_the_step_just_placed_comes_next(self):
        ordered, _, _ = order_with_links(self.concepts, [(1, 3)], build_on_latest=True)

        self.assertEqual([concept.id for concept in ordered], [1, 3, 2])

    def test_pdf_order_breaks_ties_by_default(self):
        ordered, _, _ = order_with_links(self.concepts, [(1, 3)])

        self.assertEqual([concept.id for concept in ordered], [1, 2, 3])

    def test_a_loop_is_broken_at_its_weakest_link(self):
        ordered, _, ignored = order_with_links(self.concepts, [(1, 2), (2, 1)], {(1, 2): 0.9, (2, 1): 0.2})

        self.assertEqual(ignored, [(2, 1)])
        self.assertEqual([concept.id for concept in ordered][:2], [1, 2])


class StoredConfidenceTests(PublishingFixture):
    def test_a_v4_row_without_confidence_reads_as_zero(self):
        ConceptPrerequisite.objects.create(
            outline_node=self.topic, prerequisite=self.groups["Matter"], dependent=self.groups["Solid"],
            status="accepted", source="derived", evidence={"rule": "containment", "containment": {"heading": "matter"}},
        )
        ids = {group.id for group in self.groups.values()}

        self.assertEqual(path_link_confidence(self.topic, ids), {(self.groups["Matter"].id, self.groups["Solid"].id): 0.0})

    def test_a_redundant_prerequisite_is_flagged_for_the_screen(self):
        for before, after in (("Matter", "Solid"), ("Solid", "Liquid"), ("Matter", "Liquid")):
            ConceptPrerequisite.objects.create(
                outline_node=self.topic, prerequisite=self.groups[before], dependent=self.groups[after],
                status="approved", source="teacher",
            )

        with self._derive():
            path = build_topic_path(self.topic.id)

        liquid = next(step for step in path["steps"] if step["title"] == "Liquid")
        flags = {entry["title"]: entry["redundant"] for entry in liquid["prerequisites"]}
        self.assertEqual(flags, {"Matter": True, "Solid": False})


class RedundantAcrossLoopTests(PublishingFixture):
    def test_a_link_is_not_called_redundant_through_a_loop_that_gets_broken(self):
        """Review finding: Matter -> Solid was hidden because the search walked
        Matter -> Liquid -> Matter -> Solid through a loop Kahn then breaks."""
        ConceptPrerequisite.objects.create(
            outline_node=self.topic, prerequisite=self.groups["Matter"], dependent=self.groups["Solid"],
            status="approved", source="teacher",
        )
        for before, after, confidence in (("Matter", "Liquid", 0.5), ("Liquid", "Matter", 0.6)):
            ConceptPrerequisite.objects.create(
                outline_node=self.topic, prerequisite=self.groups[before], dependent=self.groups[after],
                status="accepted", source="derived", evidence={"rule": "fusion", "confidence": confidence},
            )

        with self._derive():
            path = build_topic_path(self.topic.id)

        solid = next(step for step in path["steps"] if step["title"] == "Solid")
        self.assertEqual([entry["redundant"] for entry in solid["prerequisites"]], [False])


class CourseRefreshOnPublishTests(PublishingFixture):
    def test_publishing_a_topic_refreshes_its_course_links(self):
        with self._derive(), patch("learning_path.services.course_links.refresh_course_links", return_value={"accepted": 0, "pending": 0, "teacher_decided": 0}) as refresh:
            summary = publishing.publish_learning_path(self.topic)

        refresh.assert_called_once_with(self.topic.course)
        self.assertEqual(summary["course_links"]["accepted"], 0)

    def test_a_failed_course_refresh_never_fails_the_publish(self):
        with self._derive(), patch("learning_path.services.course_links.refresh_course_links", side_effect=RuntimeError("boom")):
            with self.assertLogs("learning_path.services.publishing", level="WARNING"):
                summary = publishing.publish_learning_path(self.topic)

        self.assertIsNone(summary["course_links"])
        self.assertEqual(summary["steps"], 4)

    def test_the_course_refresh_runs_in_its_own_savepoint(self):
        """On PostgreSQL a failed statement aborts the whole transaction; a
        savepoint lets a caller's transaction survive a failed course refresh
        (for example before the course-link table exists). SQLite cannot show
        the abort itself, so this checks the savepoint."""
        from django.db import connection, transaction

        seen = {}

        def record_savepoints(course):
            seen["savepoints"] = len(connection.savepoint_ids)
            return {"accepted": 0, "pending": 0, "teacher_decided": 0}

        with transaction.atomic():
            outer = len(connection.savepoint_ids)
            with self._derive(), patch("learning_path.services.course_links.refresh_course_links", side_effect=record_savepoints):
                publishing.publish_learning_path(self.topic)

        self.assertGreater(seen["savepoints"], outer)
