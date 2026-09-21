"""Reviewing where edited learning objects belong.

Grouping is sticky by design, so an edit to a grouped object is otherwise never
noticed. These tests pin the rules the teacher agreed to: only real edits count,
an object moves only when it no longer fits, a teacher's own grouping is never
overruled by default, nothing changes without approval, and applying a change
to a published topic unpublishes it.
"""

import os
from unittest.mock import patch

from django.test import TestCase

from course.models import LessonVariant
from course.version_assignment import bundle_roles

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    LearningObjectMatchSuggestion,
    OutlineNode,
    grouping_fingerprint,
)
from .services import semantic_grouping as semantic
from .services.learning_resource_linker import (
    prior_grouping_fingerprints,
    record_teacher_match_decision,
)
from .services.regrouping import (
    MOVE,
    SEPARATE,
    STAY,
    apply_regrouping,
    changed_learning_objects,
    propose_regrouping,
)
from .test_semantic_grouping import FakeRuntime
from .tests import authenticated_api_client

SEMANTIC_ENV = {
    "SEMANTIC_GROUPING_MODE": "auto",
    "SEMANTIC_GROUPING_CALIBRATION": "",
    "SEMANTIC_GROUPING_AUTO_THRESHOLD": "",
    "SEMANTIC_GROUPING_REVIEW_THRESHOLD": "",
    "SEMANTIC_GROUPING_MINIMUM_SBERT_COSINE": "",
    "SEMANTIC_GROUPING_MINIMUM_MARGIN": "",
}

EXAMPLES_A = "Ice cubes, rocks and coins are everyday objects."
EXAMPLES_B = "A book and a chair are things we see every day."
SOLID_A = "A solid keeps its shape and its volume."
SOLID_C = "Solids have particles packed closely together."
EDITED_B = "A book and a chair are solids because they keep their shape."


class RegroupingFixture(TestCase):
    """Two concepts, each assembled from two of three lesson files."""

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=self.course, title="Solid, Liquid and Gas", order=0, depth=0,
        )
        self.first = self._material("Lesson one")
        self.second = self._material("Lesson two")
        self.third = self._material("Lesson three")

        self.examples = LearningObjectGroup.objects.create(outline_node=self.node, label="Examples")
        self.solid = LearningObjectGroup.objects.create(outline_node=self.node, label="Solid")

        self.examples_a = self._object(self.first, 0, "Examples", EXAMPLES_A, self.examples)
        self.examples_b = self._object(self.second, 0, "Examples", EXAMPLES_B, self.examples)
        self.solid_a = self._object(self.first, 1, "Solid", SOLID_A, self.solid)
        self.solid_c = self._object(self.third, 0, "Solid", SOLID_C, self.solid)

        env = patch.dict(os.environ, SEMANTIC_ENV)
        env.start()
        self.addCleanup(env.stop)

    def _material(self, title, confirmed=True):
        return LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title=title,
            status="completed",
            generated_json={"learning_objects_confirmed": confirmed},
        )

    def _object(self, material, order, title, content, group):
        return LearningObject.objects.create(
            material=material, group=group, title=title, content=content, order=order,
        )

    def _edit(self, learning_object, content):
        """What the teacher's edit endpoint does: change the text, nothing else."""
        learning_object.content = content
        learning_object.save()

    def _runtime(self, scores):
        return patch.object(semantic, "runtime", return_value=FakeRuntime(scores))

    def _proposal_for(self, proposals, learning_object):
        return next(row for row in proposals if row["learning_object_id"] == learning_object.id)


class ChangeDetectionTests(RegroupingFixture):
    def test_nothing_is_changed_until_something_is_edited(self):
        self.assertEqual(changed_learning_objects(self.node), [])

    def test_editing_a_grouped_object_is_a_change(self):
        self._edit(self.examples_b, EDITED_B)

        self.assertEqual(changed_learning_objects(self.node), [self.examples_b])

    def test_re_flowing_whitespace_is_not_a_change(self):
        self._edit(self.examples_b, "  A book and a chair\n are things   we see every day. ")

        self.assertEqual(changed_learning_objects(self.node), [])

    def test_a_standalone_object_is_left_to_ordinary_confirmation(self):
        """A standalone object is already re-matched every time its file is
        confirmed, so it never needs this review."""
        alone = LearningObjectGroup.objects.create(outline_node=self.node, label="Gas")
        gas = self._object(self.first, 2, "Gas", "A gas spreads out.", alone)
        self._edit(gas, "A gas fills its container.")

        self.assertEqual(changed_learning_objects(self.node), [])

    def test_an_edit_in_an_unconfirmed_file_waits_for_confirmation(self):
        self._edit(self.examples_b, EDITED_B)
        self.second.generated_json = {"learning_objects_confirmed": False}
        self.second.save()

        self.assertEqual(changed_learning_objects(self.node), [])

    def test_regeneration_carries_the_grouped_fingerprint_forward(self):
        """Re-extraction recreates every object and restores its group by title
        and position. The old fingerprint has to come along, or a passage whose
        wording changed slips back into its group as if never edited."""
        before = prior_grouping_fingerprints(self.second)

        self.assertEqual(
            before[("examples", 0)],
            grouping_fingerprint("Examples", EXAMPLES_B),
        )


class ProposalTests(RegroupingFixture):
    def test_an_object_that_still_fits_stays(self):
        self._edit(self.examples_b, "A book and a chair are things we see daily.")

        with self._runtime({}):  # everything scores 95%
            proposal = propose_regrouping(self.node)[0]

        self.assertEqual(proposal["action"], STAY)
        self.assertFalse(proposal["selectable"])

    def test_an_object_that_no_longer_fits_moves_to_a_clear_match(self):
        self._edit(self.examples_b, EDITED_B)

        with self._runtime({EXAMPLES_A: 0.20}):
            proposal = propose_regrouping(self.node)[0]

        self.assertEqual(proposal["action"], MOVE)
        self.assertEqual(proposal["destination_group"]["id"], self.solid.id)
        self.assertTrue(proposal["default_selected"])

    def test_with_no_clear_match_it_would_stand_alone(self):
        self._edit(self.examples_b, EDITED_B)

        with self._runtime({EXAMPLES_A: 0.20, SOLID_A: 0.10, SOLID_C: 0.10}):
            proposal = propose_regrouping(self.node)[0]

        self.assertEqual(proposal["action"], SEPARATE)
        self.assertIsNone(proposal["destination_group"])

    def test_its_current_group_is_never_proposed_as_the_destination(self):
        self._edit(self.examples_b, EDITED_B)

        with self._runtime({EXAMPLES_A: 0.20, SOLID_A: 0.10, SOLID_C: 0.10}):
            proposal = propose_regrouping(self.node)[0]

        self.assertNotEqual((proposal["destination_group"] or {}).get("id"), self.examples.id)

    def test_a_teachers_grouping_is_proposed_but_left_unticked(self):
        record_teacher_match_decision(self.examples_a, self.examples_b, accepted=True)
        self._edit(self.examples_b, EDITED_B)

        with self._runtime({EXAMPLES_A: 0.20}):
            proposal = propose_regrouping(self.node)[0]

        self.assertTrue(proposal["teacher_made"])
        self.assertTrue(proposal["selectable"])
        self.assertFalse(proposal["default_selected"])

    def test_previewing_changes_nothing(self):
        self._edit(self.examples_b, EDITED_B)

        with self._runtime({EXAMPLES_A: 0.20}):
            propose_regrouping(self.node)

        self.examples_b.refresh_from_db()
        self.assertEqual(self.examples_b.group_id, self.examples.id)
        self.assertEqual(changed_learning_objects(self.node), [self.examples_b])


class ApplyTests(RegroupingFixture):
    def test_a_chosen_move_is_carried_out(self):
        self._edit(self.examples_b, EDITED_B)

        with self._runtime({EXAMPLES_A: 0.20}):
            summary = apply_regrouping(self.node, [self.examples_b.id])

        self.examples_b.refresh_from_db()
        self.assertEqual(self.examples_b.group_id, self.solid.id)
        self.assertEqual(len(summary["applied"]), 1)
        # Its old companion is left where it was.
        self.examples_a.refresh_from_db()
        self.assertEqual(self.examples_a.group_id, self.examples.id)

    def test_the_move_is_recorded_as_the_teachers_decision(self):
        """So background matching never pulls the object back."""
        self._edit(self.examples_b, EDITED_B)

        with self._runtime({EXAMPLES_A: 0.20}):
            apply_regrouping(self.node, [self.examples_b.id])

        self.assertTrue(
            LearningObjectMatchSuggestion.objects.filter(
                status=LearningObjectMatchSuggestion.Status.REJECTED,
                source_learning_object=self.examples_a,
                candidate_learning_object=self.examples_b,
            ).exists()
        )

    def test_an_unticked_proposal_is_not_applied_but_is_settled(self):
        """The teacher has seen it and chose to leave it, so the review must
        not keep asking about the same decision."""
        self._edit(self.examples_b, EDITED_B)

        with self._runtime({EXAMPLES_A: 0.20}):
            summary = apply_regrouping(self.node, [])

        self.examples_b.refresh_from_db()
        self.assertEqual(self.examples_b.group_id, self.examples.id)
        self.assertEqual(summary["applied"], [])
        self.assertEqual(changed_learning_objects(self.node), [])

    def test_an_id_the_review_did_not_propose_is_ignored(self):
        """Proposals are recomputed server-side; a request cannot move an
        object the review would not have offered."""
        self._edit(self.examples_b, EDITED_B)

        with self._runtime({EXAMPLES_A: 0.20}):
            apply_regrouping(self.node, [self.solid_a.id])

        self.solid_a.refresh_from_db()
        self.assertEqual(self.solid_a.group_id, self.solid.id)

    def test_a_group_left_empty_is_removed(self):
        lonely_group = LearningObjectGroup.objects.create(outline_node=self.node, label="Pair")
        left = self._object(self.first, 5, "Pair", "Left text.", lonely_group)
        right = self._object(self.second, 5, "Pair", "Right text.", lonely_group)
        self._edit(left, EDITED_B)
        self._edit(right, EDITED_B + " Again.")

        with self._runtime({"Left text.": 0.1, "Right text.": 0.1, EDITED_B: 0.1, EDITED_B + " Again.": 0.1}):
            apply_regrouping(self.node, [left.id, right.id])

        self.assertFalse(LearningObjectGroup.objects.filter(pk=lonely_group.id).exists())

    def test_applying_a_change_unpublishes_the_topic(self):
        self.node.published = True
        self.node.save()
        self._edit(self.examples_b, EDITED_B)

        with self._runtime({EXAMPLES_A: 0.20}):
            summary = apply_regrouping(self.node, [self.examples_b.id])

        self.node.refresh_from_db()
        self.assertFalse(self.node.published)
        self.assertTrue(summary["unpublished"])

    def test_applying_nothing_keeps_the_topic_published(self):
        self.node.published = True
        self.node.save()
        self._edit(self.examples_b, EDITED_B)

        with self._runtime({EXAMPLES_A: 0.20}):
            apply_regrouping(self.node, [])

        self.node.refresh_from_db()
        self.assertTrue(self.node.published)

    def test_when_the_original_leaves_its_companions_are_released(self):
        """The leaving object supplied the concept's Normal version. Text it
        held for its companions goes, and the concept picks a new original."""
        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        self.examples_a.represented_by = self.examples_b
        self.examples_a.save()
        self.examples.version_selection = {
            "normal_material_id": self.second.id,
            "bundle_roles": {str(self.first.id): "SIMPLIFIED"},
            "bundle_roles_assigned_by": {str(self.first.id): "teacher"},
        }
        self.examples.save()
        self._edit(self.examples_b, EDITED_B)

        with self._runtime({EXAMPLES_A: 0.20}):
            proposal = propose_regrouping(self.node)[0]
            apply_regrouping(self.node, [self.examples_b.id])

        self.assertTrue(proposal["impact"]["was_original"])
        self.assertEqual(proposal["impact"]["removed_version_slots"], ["simplified"])
        self.examples_a.refresh_from_db()
        self.examples.refresh_from_db()
        self.assertIsNone(self.examples_a.represented_by_id)
        self.assertEqual(self.examples.version_selection, {})

    def test_a_leaving_member_takes_its_own_text_off_the_original(self):
        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        self.examples_b.represented_by = self.examples_a
        self.examples_b.save()
        self.examples.version_selection = {
            "normal_material_id": self.first.id,
            "bundle_roles": {str(self.second.id): "ELABORATED"},
            "bundle_roles_assigned_by": {str(self.second.id): "teacher"},
        }
        self.examples.save()
        generated = LessonVariant.objects.create(
            learning_object=self.examples_a, variant="SIMPLIFIED",
            narration="Short.", origin=LessonVariant.Origin.GENERATED,
            assigned_by=LessonVariant.AssignedBy.TEACHER,
        )
        self._edit(self.examples_b, EDITED_B)

        with self._runtime({EXAMPLES_A: 0.20}):
            apply_regrouping(self.node, [self.examples_b.id])

        self.examples_b.refresh_from_db()
        self.examples.refresh_from_db()
        self.assertIsNone(self.examples_b.represented_by_id)
        self.assertNotIn(self.second.id, bundle_roles(self.examples))
        # A teacher's edit to a generated version is never discarded here.
        self.assertTrue(LessonVariant.objects.filter(pk=generated.pk).exists())


class GroupingDecisionFingerprintTests(RegroupingFixture):
    def test_connecting_objects_settles_their_fingerprint(self):
        self._edit(self.examples_b, EDITED_B)
        client = authenticated_api_client()

        with self._runtime({}):
            response = client.post(
                f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/connect-learning-objects/",
                {"learning_object_ids": [self.examples_b.id, self.solid_a.id]},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(changed_learning_objects(self.node), [])

    def test_separating_an_object_settles_its_fingerprint(self):
        self._edit(self.examples_b, EDITED_B)
        client = authenticated_api_client()

        with self._runtime({}):
            response = client.post(
                f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/separate-learning-object/",
                {"learning_object_id": self.examples_b.id},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.examples_b.refresh_from_db()
        self.assertEqual(
            self.examples_b.grouping_content_hash,
            grouping_fingerprint("Examples", EDITED_B),
        )


class SeparateReleasesVersionLinksTests(RegroupingFixture):
    """Regression: moving an object out of a group used to keep it "taught
    through" its old group's original. Generating its versions was refused, and
    its text kept serving as that original's version for another concept."""

    def _link_as_member(self):
        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        self.examples_b.represented_by = self.examples_a
        self.examples_b.save()
        self.examples.version_selection = {
            "normal_material_id": self.first.id,
            "bundle_roles": {str(self.second.id): "EXTRA"},
            "bundle_roles_assigned_by": {str(self.second.id): "teacher"},
        }
        self.examples.save()

    def _post(self, path, data):
        client = authenticated_api_client()
        with self._runtime({}):
            return client.post(
                f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/{path}",
                data,
                format="json",
            )

    def test_separate_undoes_the_old_groups_version_links(self):
        self._link_as_member()

        response = self._post("separate-learning-object/", {"learning_object_id": self.examples_b.id})

        self.assertEqual(response.status_code, 200, response.data)
        self.examples_b.refresh_from_db()
        self.examples.refresh_from_db()
        self.assertIsNone(self.examples_b.represented_by_id)
        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        self.assertNotIn(self.second.id, bundle_roles(self.examples))

    def test_connecting_it_elsewhere_undoes_the_old_groups_version_links(self):
        # Connect keeps the first selected object's group, so here Examples is
        # the destination and it is Solid (from lesson one) that moves, leaving
        # its lesson-three companion behind.
        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        self.solid_a.represented_by = self.solid_c
        self.solid_a.save()
        self.solid.version_selection = {
            "normal_material_id": self.third.id,
            "bundle_roles": {str(self.first.id): "SIMPLIFIED"},
            "bundle_roles_assigned_by": {str(self.first.id): "teacher"},
        }
        self.solid.save()

        response = self._post(
            "connect-learning-objects/",
            {"learning_object_ids": [self.examples_b.id, self.solid_a.id]},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.solid_a.refresh_from_db()
        self.solid.refresh_from_db()
        self.assertEqual(self.solid_a.group_id, self.examples.id)
        self.assertIsNone(self.solid_a.represented_by_id)
        self.assertNotIn(self.first.id, bundle_roles(self.solid))

    def test_generating_versions_for_a_separated_object_is_no_longer_refused(self):
        self._link_as_member()
        self._post("separate-learning-object/", {"learning_object_id": self.examples_b.id})

        with patch("lessons.views.fill_missing_slots", return_value={"generated": [], "errors": []}):
            response = self._post(
                f"learning-objects/{self.examples_b.id}/generate-versions/", {},
            )

        self.assertNotEqual(
            (response.data or {}).get("detail"),
            "This object is taught through another one; generate versions there.",
        )


class RegroupingEndpointTests(RegroupingFixture):
    def setUp(self):
        super().setUp()
        self.client_api = authenticated_api_client()
        self.base = f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}"

    def test_the_resources_payload_reports_how_many_objects_changed(self):
        self._edit(self.examples_b, EDITED_B)

        response = self.client_api.get(f"{self.base}/learning-resources/")

        self.assertEqual(response.data["regrouping"], {"changed_count": 1})

    def test_the_preview_endpoint_returns_proposals(self):
        self._edit(self.examples_b, EDITED_B)

        with self._runtime({EXAMPLES_A: 0.20}):
            response = self.client_api.get(f"{self.base}/regrouping/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["proposals"][0]["action"], MOVE)

    def test_the_apply_endpoint_moves_and_returns_fresh_resources(self):
        self._edit(self.examples_b, EDITED_B)

        with self._runtime({EXAMPLES_A: 0.20}):
            response = self.client_api.post(
                f"{self.base}/regrouping/apply/",
                {"learning_object_ids": [self.examples_b.id]},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["summary"]["applied"]), 1)
        self.assertEqual(response.data["resources"]["regrouping"], {"changed_count": 0})

    def test_the_apply_endpoint_rejects_a_malformed_list(self):
        response = self.client_api.post(
            f"{self.base}/regrouping/apply/",
            {"learning_object_ids": "all"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
