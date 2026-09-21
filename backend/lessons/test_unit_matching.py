"""Units: several objects of one PDF that another PDF teaches as one.

Scores alone cannot find them (measured: every item scored 0.5-0.65 against
every section), and structure alone over-merges (a "Matter" section holding
Matter, Solid, Liquid and Gas). A unit is proposed only when a heading in the
other PDF names it, and never merged without the teacher.
"""

import os
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    LearningObjectMatchSuggestion,
    OutlineNode,
)
from .services.unit_matching import (
    find_units,
    heading_key,
    heading_unit_candidates,
    refresh_heading_unit_suggestions,
)
from .test_semantic_grouping import FakeRuntime
from .tests import authenticated_api_client


class UnitFixture(TestCase):
    """Shaped like topic 62: material A itemises, material B uses sections."""

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.a = LearningMaterial.objects.create(course=self.course, outline_node=self.topic, title="A", generated_json=dict(confirmed))
        self.b = LearningMaterial.objects.create(course=self.course, outline_node=self.topic, title="B", generated_json=dict(confirmed))
        o = self._object
        self.matter = o(self.a, "Matter", "Matter", 0)
        self.solid_a = o(self.a, "Solid", "Matter", 1)
        self.liquid_a = o(self.a, "Liquid", "Matter", 2)
        self.shape = o(self.a, "Shape", "Comparing the Three States", 3)
        self.volume = o(self.a, "Volume", "Comparing the Three States", 4)
        self.examples_a = o(self.a, "Everyday Examples", "", 5)
        self.solids_b = o(self.b, "Solids", "Solids", 0)
        self.diagram_b = o(self.b, "Diagram description", "Solids", 1)
        self.liquids_b = o(self.b, "Liquids", "Liquids", 2)
        self.compare_b = o(self.b, "Comparing the Three States", "Comparing the Three States", 3)
        self.table_b = o(self.b, "5. Comparing the Three States", "", 4, kind="image")
        self.changing_b = o(self.b, "Changing From One State to Another", "", 5)
        self.changing_fig = o(self.b, "6. Changing From One State to Another", "", 6, kind="image")
        self.examples_b = o(self.b, "Everyday Examples", "Everyday Examples", 7)
        self.examples_table = o(self.b, "7. Everyday Examples", "", 8, kind="image")

    def _object(self, material, title, section, order, kind="text"):
        group = LearningObjectGroup.objects.create(outline_node=self.topic, label=title)
        return LearningObject.objects.create(
            material=material, group=group, title=title, content=f"{title} text.",
            section_title=section, order=order, kind=kind,
        )

    def _labels(self, candidates):
        return {
            (c["label"], tuple(item.id for item in c["left"]), tuple(item.id for item in c["right"]))
            for c in candidates
        }


class HeadingKeyTests(UnitFixture):
    def test_numbering_case_and_plural_fold_away(self):
        self.assertEqual(heading_key("5. Comparing the Three States"), "comparing the three state")
        self.assertEqual(heading_key("Solids"), heading_key("Solid"))


class FindUnitTests(UnitFixture):
    def test_units_follow_shared_sections_and_repeated_titles(self):
        units_b = {unit.label: unit.ids for unit in find_units(list(self.b.learning_objects.all()))}

        self.assertEqual(units_b[heading_key("Solids")], [self.solids_b.id, self.diagram_b.id])
        self.assertEqual(units_b[heading_key("Comparing the Three States")], [self.compare_b.id, self.table_b.id])
        self.assertEqual(units_b[heading_key("Changing From One State to Another")], [self.changing_b.id, self.changing_fig.id])
        self.assertEqual(units_b[heading_key("Everyday Examples")], [self.examples_b.id, self.examples_table.id])
        self.assertNotIn(heading_key("Liquids"), units_b)

    def test_a_section_of_itemised_concepts_is_still_a_unit(self):
        units_a = {unit.label: unit.ids for unit in find_units(list(self.a.learning_objects.all()))}

        self.assertEqual(units_a["matter"], [self.matter.id, self.solid_a.id, self.liquid_a.id])


class CandidateTests(UnitFixture):
    def test_units_are_proposed_only_where_the_other_pdf_names_them(self):
        labels = self._labels(heading_unit_candidates(self.topic))

        self.assertIn(
            (heading_key("Comparing the Three States"), (self.shape.id, self.volume.id), (self.compare_b.id, self.table_b.id)),
            labels,
        )
        self.assertIn(("solid", (self.solid_a.id,), (self.solids_b.id, self.diagram_b.id)), labels)
        self.assertIn(
            (heading_key("Everyday Examples"), (self.examples_a.id,), (self.examples_b.id, self.examples_table.id)),
            labels,
        )
        self.assertFalse(any(label == "matter" for label, _, _ in labels))
        self.assertFalse(any(label == heading_key("Changing From One State to Another") for label, _, _ in labels))

    def test_a_unit_whose_members_already_agree_across_pdfs_is_not_proposed(self):
        self.solids_b.group = self.solid_a.group
        self.solids_b.save(update_fields=["group"])
        self.diagram_b.group = self.liquid_a.group
        self.diagram_b.save(update_fields=["group"])

        labels = self._labels(heading_unit_candidates(self.topic))

        self.assertFalse(any(label == "solid" for label, _, _ in labels))

    def test_an_ambiguous_label_is_not_proposed(self):
        self._object(self.a, "Solid", "", 9)

        labels = self._labels(heading_unit_candidates(self.topic))

        self.assertFalse(any(label == "solid" for label, _, _ in labels))


SEMANTIC_ENV = {
    "SEMANTIC_GROUPING_MODE": "auto",
    "SEMANTIC_GROUPING_CALIBRATION": "",
    "SEMANTIC_GROUPING_AUTO_THRESHOLD": "",
    "SEMANTIC_GROUPING_REVIEW_THRESHOLD": "",
    "SEMANTIC_GROUPING_MINIMUM_SBERT_COSINE": "",
    "SEMANTIC_GROUPING_MINIMUM_MARGIN": "",
}


class PlacementTests(UnitFixture):
    def _refresh(self, score):
        runtime = FakeRuntime()
        runtime.pair_scores = lambda pairs: [score for _ in pairs]
        with patch.dict(os.environ, SEMANTIC_ENV):
            return refresh_heading_unit_suggestions(self.topic, runtime_instance=runtime)

    def test_a_confident_match_is_placed_without_a_card(self):
        emptied_group_id = self.solids_b.group_id

        counts = self._refresh(0.8)

        self.assertFalse(LearningObjectGroup.objects.filter(pk=emptied_group_id).exists())
        self.solids_b.refresh_from_db()
        self.diagram_b.refresh_from_db()
        self.solid_a.refresh_from_db()
        self.assertEqual(self.solids_b.group_id, self.solid_a.group_id)
        self.assertEqual(self.diagram_b.group_id, self.solid_a.group_id)
        self.assertGreaterEqual(counts["placed"], 1)
        self.assertFalse(
            LearningObjectMatchSuggestion.objects.filter(
                status=LearningObjectMatchSuggestion.Status.PENDING,
                evidence__label=heading_key("Solids"),
            ).exists()
        )

    def test_an_uncertain_match_raises_a_card_and_places_nothing(self):
        self._refresh(0.45)

        self.solids_b.refresh_from_db()
        self.diagram_b.refresh_from_db()
        self.assertNotEqual(self.solids_b.group_id, self.solid_a.group_id)
        self.assertNotEqual(self.diagram_b.group_id, self.solid_a.group_id)
        card = LearningObjectMatchSuggestion.objects.get(
            status=LearningObjectMatchSuggestion.Status.PENDING,
            evidence__label="solid",
        )
        self.assertEqual(
            {card.source_learning_object_id, card.candidate_learning_object_id,
             *card.source_extra_ids, *card.candidate_extra_ids},
            {self.solid_a.id, self.solids_b.id, self.diagram_b.id},
        )

    def test_a_score_below_the_review_threshold_does_nothing(self):
        self._refresh(0.1)

        self.solids_b.refresh_from_db()
        self.assertNotEqual(self.solids_b.group_id, self.solid_a.group_id)
        self.assertFalse(LearningObjectMatchSuggestion.objects.exists())

    def test_a_section_of_separate_concepts_is_never_placed(self):
        """Regression: the Matter heading covers Matter, Solid and Liquid in
        material A; no PDF names that run, so it must not fuse."""
        self._refresh(0.8)

        self.matter.refresh_from_db()
        self.solid_a.refresh_from_db()
        self.assertNotEqual(self.matter.group_id, self.solid_a.group_id)

    def test_accepting_a_card_places_the_objects_without_merging(self):
        self._refresh(0.45)
        suggestion = LearningObjectMatchSuggestion.objects.filter(
            status=LearningObjectMatchSuggestion.Status.PENDING).first()
        client = authenticated_api_client()

        response = client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}"
            f"/match-suggestions/{suggestion.id}/accept/",
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        members = {
            item.id for item in LearningObject.objects.filter(
                group_id=LearningObject.objects.get(pk=suggestion.source_learning_object_id).group_id)
        }
        expected = {
            suggestion.source_learning_object_id, suggestion.candidate_learning_object_id,
            *suggestion.source_extra_ids, *suggestion.candidate_extra_ids,
        }
        self.assertEqual(members & expected, expected)
        self.assertEqual(LearningObject.objects.filter(pk__in=expected).count(), len(expected))

    def test_a_card_is_pending_and_carries_every_member(self):
        self._refresh(0.45)

        suggestion = LearningObjectMatchSuggestion.objects.get(
            evidence__label=heading_key("Comparing the Three States"),
        )
        self.assertEqual(suggestion.status, LearningObjectMatchSuggestion.Status.PENDING)
        self.assertEqual(suggestion.evidence["method"], "heading_unit_v1")
        members = {suggestion.source_learning_object_id, suggestion.candidate_learning_object_id,
                   *suggestion.source_extra_ids, *suggestion.candidate_extra_ids}
        self.assertEqual(members, {self.shape.id, self.volume.id, self.compare_b.id, self.table_b.id})

    def test_a_rejected_unit_suggestion_is_not_reopened(self):
        self._refresh(0.45)
        LearningObjectMatchSuggestion.objects.update(status=LearningObjectMatchSuggestion.Status.REJECTED)

        self._refresh(0.45)

        self.assertFalse(
            LearningObjectMatchSuggestion.objects.filter(status=LearningObjectMatchSuggestion.Status.PENDING).exists()
        )


class OccupiedPairTests(UnitFixture):
    """A one-to-one accept can leave a unit suggestion's natural lead pair
    already occupied by a real decision; that decision must never be touched."""

    def _refresh(self, score=0.5):
        runtime = FakeRuntime()
        runtime.pair_scores = lambda pairs: [score for _ in pairs]
        with patch.dict(os.environ, SEMANTIC_ENV):
            return refresh_heading_unit_suggestions(self.topic, runtime_instance=runtime)

    def _accept_one_to_one(self, source, candidate):
        client = authenticated_api_client()
        suggestion = LearningObjectMatchSuggestion.objects.create(
            outline_node=self.topic,
            source_learning_object=source,
            candidate_learning_object=candidate,
            similarity_score=0.9,
            confidence=LearningObjectMatchSuggestion.Confidence.HIGH,
            status=LearningObjectMatchSuggestion.Status.PENDING,
            evidence={"method": "content_sts_v1"},
        )
        response = client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}/match-suggestions/{suggestion.id}/accept/",
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        return suggestion

    def test_an_accepted_one_to_one_pair_is_not_reset_by_a_colliding_unit_suggestion(self):
        source, candidate = sorted([self.solid_a, self.solids_b], key=lambda item: item.id)
        accepted = self._accept_one_to_one(source, candidate)

        self._refresh()

        accepted.refresh_from_db()
        self.assertEqual(accepted.status, LearningObjectMatchSuggestion.Status.ACCEPTED)
        unit_suggestion = LearningObjectMatchSuggestion.objects.get(evidence__label="solid")
        self.assertEqual(unit_suggestion.status, LearningObjectMatchSuggestion.Status.PENDING)
        members = {
            unit_suggestion.source_learning_object_id, unit_suggestion.candidate_learning_object_id,
            *unit_suggestion.source_extra_ids, *unit_suggestion.candidate_extra_ids,
        }
        self.assertEqual(members, {self.solid_a.id, self.solids_b.id, self.diagram_b.id})

    def test_accepting_the_rekeyed_unit_suggestion_places_and_keeps_the_prior_accept(self):
        source, candidate = sorted([self.solid_a, self.solids_b], key=lambda item: item.id)
        accepted = self._accept_one_to_one(source, candidate)
        self._refresh()
        unit_suggestion = LearningObjectMatchSuggestion.objects.get(evidence__label="solid")
        client = authenticated_api_client()

        response = client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}"
            f"/match-suggestions/{unit_suggestion.id}/accept/",
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.solid_a.refresh_from_db()
        self.solids_b.refresh_from_db()
        self.diagram_b.refresh_from_db()
        self.assertEqual(self.solids_b.group_id, self.solid_a.group_id)
        self.assertEqual(self.diagram_b.group_id, self.solid_a.group_id)
        accepted.refresh_from_db()
        self.assertEqual(accepted.status, LearningObjectMatchSuggestion.Status.ACCEPTED)


class UnitDecisionTests(UnitFixture):
    """Teacher decisions on unit suggestions are recorded and never overwritten."""

    def _refresh(self, score=0.5):
        runtime = FakeRuntime()
        runtime.pair_scores = lambda pairs: [score for _ in pairs]
        with patch.dict(os.environ, SEMANTIC_ENV):
            return refresh_heading_unit_suggestions(self.topic, runtime_instance=runtime)

    def _url(self, suggestion, verb):
        return (
            f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}"
            f"/match-suggestions/{suggestion.id}/{verb}/"
        )

    def _unit_row(self, **fields):
        return LearningObjectMatchSuggestion.objects.create(
            outline_node=self.topic,
            source_learning_object=self.solid_a,
            candidate_learning_object=self.diagram_b,
            similarity_score=0.5,
            confidence=LearningObjectMatchSuggestion.Confidence.MEDIUM,
            status=LearningObjectMatchSuggestion.Status.PENDING,
            candidate_extra_ids=[self.solids_b.id],
            evidence={"method": "heading_unit_v1", "label": "solid"},
            **fields,
        )

    def test_declining_a_unit_suggestion_whose_lead_rows_differ_in_kind_is_recorded(self):
        LearningObject.objects.filter(pk=self.examples_a.id).update(kind="image")
        self._refresh()
        suggestion = LearningObjectMatchSuggestion.objects.get(evidence__label=heading_key("Everyday Examples"))
        kinds = {suggestion.source_learning_object.kind, suggestion.candidate_learning_object.kind}
        self.assertEqual(kinds, {"image", "text"})
        extras = (list(suggestion.source_extra_ids), list(suggestion.candidate_extra_ids))

        response = authenticated_api_client().post(self._url(suggestion, "reject"), format="json")

        self.assertEqual(response.status_code, 200, response.data)
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, LearningObjectMatchSuggestion.Status.REJECTED)
        self.assertTrue(suggestion.evidence["teacher_reviewed"])
        self.assertEqual(suggestion.evidence["teacher_decision"], "rejected")
        self.assertEqual(suggestion.evidence["method"], "heading_unit_v1")
        self.assertEqual((suggestion.source_extra_ids, suggestion.candidate_extra_ids), extras)

    def test_a_declined_unit_stays_declined_when_its_members_change(self):
        self._refresh()
        suggestion = LearningObjectMatchSuggestion.objects.get(evidence__label="solid")
        response = authenticated_api_client().post(self._url(suggestion, "reject"), format="json")
        self.assertEqual(response.status_code, 200, response.data)
        suggestion.refresh_from_db()
        extras = (list(suggestion.source_extra_ids), list(suggestion.candidate_extra_ids))
        self._object(self.b, "Particles in a solid", "Solids", 1)

        self._refresh()

        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, LearningObjectMatchSuggestion.Status.REJECTED)
        self.assertEqual((suggestion.source_extra_ids, suggestion.candidate_extra_ids), extras)

    def test_a_failure_after_placing_rolls_the_whole_accept_back(self):
        self._refresh()
        suggestion = LearningObjectMatchSuggestion.objects.get(
            evidence__label=heading_key("Comparing the Three States"),
        )
        groups = {
            item.id: item.group_id
            for item in LearningObject.objects.filter(
                pk__in=[self.shape.id, self.volume.id, self.compare_b.id, self.table_b.id],
            )
        }

        with patch(
            "lessons.views.CourseGroupViewSet._refresh_relationship_snapshots",
            side_effect=RuntimeError("snapshot failed"),
        ):
            with self.assertRaises(RuntimeError):
                authenticated_api_client().post(self._url(suggestion, "accept"), format="json")

        self.assertEqual(
            {
                item.id: item.group_id
                for item in LearningObject.objects.filter(pk__in=list(groups))
            },
            groups,
        )
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, LearningObjectMatchSuggestion.Status.PENDING)
        self.assertTrue(suggestion.source_extra_ids or suggestion.candidate_extra_ids)

    def test_a_declined_pair_refuses_the_accept_before_placing(self):
        declined = LearningObjectMatchSuggestion.objects.create(
            outline_node=self.topic,
            source_learning_object=self.solid_a,
            candidate_learning_object=self.solids_b,
            similarity_score=0.9,
            confidence=LearningObjectMatchSuggestion.Confidence.TEACHER_CONFIRMED,
            status=LearningObjectMatchSuggestion.Status.REJECTED,
            evidence={"teacher_reviewed": True, "teacher_decision": "rejected"},
        )
        unit = self._unit_row()

        response = authenticated_api_client().post(self._url(unit, "accept"), format="json")

        self.assertEqual(response.status_code, 409)
        self.assertIn("declined", response.data["detail"])
        self.solid_a.refresh_from_db()
        self.solids_b.refresh_from_db()
        self.diagram_b.refresh_from_db()
        self.assertNotEqual(self.solids_b.group_id, self.solid_a.group_id)
        self.assertNotEqual(self.diagram_b.group_id, self.solid_a.group_id)
        declined.refresh_from_db()
        unit.refresh_from_db()
        self.assertEqual(declined.status, LearningObjectMatchSuggestion.Status.REJECTED)
        self.assertEqual(unit.status, LearningObjectMatchSuggestion.Status.PENDING)

    def test_a_decline_within_one_side_does_not_block_the_accept(self):
        """Two objects of the same PDF declined against each other say nothing
        about whether the two PDFs teach one concept; vetoing on that would
        strand the card forever."""
        LearningObjectMatchSuggestion.objects.create(
            outline_node=self.topic,
            source_learning_object=self.solids_b,
            candidate_learning_object=self.diagram_b,
            similarity_score=0.9,
            confidence=LearningObjectMatchSuggestion.Confidence.TEACHER_CONFIRMED,
            status=LearningObjectMatchSuggestion.Status.REJECTED,
            evidence={"teacher_reviewed": True, "teacher_decision": "rejected"},
        )
        unit = self._unit_row()

        response = authenticated_api_client().post(self._url(unit, "accept"), format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.solid_a.refresh_from_db()
        self.solids_b.refresh_from_db()
        self.diagram_b.refresh_from_db()
        self.assertEqual(self.solids_b.group_id, self.solid_a.group_id)
        self.assertEqual(self.diagram_b.group_id, self.solid_a.group_id)

    def test_an_extra_id_from_the_wrong_side_is_out_of_date(self):
        unit = self._unit_row(source_extra_ids=[self.liquid_a.id])
        unit.candidate_extra_ids = [self.solids_b.id, self.volume.id]
        unit.save(update_fields=["candidate_extra_ids"])

        response = authenticated_api_client().post(self._url(unit, "accept"), format="json")

        self.assertEqual(response.status_code, 409)
        self.assertIn("out of date", response.data["detail"])
        self.solid_a.refresh_from_db()
        self.solids_b.refresh_from_db()
        self.assertNotEqual(self.solids_b.group_id, self.solid_a.group_id)

    def test_an_id_repeated_across_the_two_lists_is_not_counted_twice(self):
        unit = self._unit_row(source_extra_ids=[self.solid_a.id])

        response = authenticated_api_client().post(self._url(unit, "accept"), format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.solid_a.refresh_from_db()
        self.solids_b.refresh_from_db()
        self.diagram_b.refresh_from_db()
        self.assertEqual(self.solids_b.group_id, self.solid_a.group_id)
        self.assertEqual(self.diagram_b.group_id, self.solid_a.group_id)

    def test_an_accepted_lead_pair_keeps_its_teacher_evidence(self):
        accepted = LearningObjectMatchSuggestion.objects.create(
            outline_node=self.topic,
            source_learning_object=self.solid_a,
            candidate_learning_object=self.solids_b,
            similarity_score=0.9,
            confidence=LearningObjectMatchSuggestion.Confidence.TEACHER_CONFIRMED,
            status=LearningObjectMatchSuggestion.Status.ACCEPTED,
            evidence={"method": "content_sts_v1", "teacher_reviewed": True, "teacher_decision": "accepted"},
        )
        unit = self._unit_row()

        response = authenticated_api_client().post(self._url(unit, "accept"), format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("unpublished", response.data)
        self.solid_a.refresh_from_db()
        self.solids_b.refresh_from_db()
        self.diagram_b.refresh_from_db()
        self.assertEqual(self.solids_b.group_id, self.solid_a.group_id)
        self.assertEqual(self.diagram_b.group_id, self.solid_a.group_id)
        accepted.refresh_from_db()
        self.assertEqual(accepted.status, LearningObjectMatchSuggestion.Status.ACCEPTED)
        self.assertEqual(accepted.confidence, LearningObjectMatchSuggestion.Confidence.TEACHER_CONFIRMED)
        self.assertTrue(accepted.evidence["teacher_reviewed"])
        self.assertEqual(accepted.evidence["teacher_decision"], "accepted")
        self.assertEqual((accepted.source_extra_ids, accepted.candidate_extra_ids), ([], []))


class PlacementGuardTests(UnitFixture):
    """What outranks a confident score: a decline, a locked name, a duplicate."""

    def _refresh(self, score):
        runtime = FakeRuntime()
        runtime.pair_scores = lambda pairs: [score for _ in pairs]
        with patch.dict(os.environ, SEMANTIC_ENV):
            return refresh_heading_unit_suggestions(self.topic, runtime_instance=runtime)

    def test_a_rejected_unit_is_never_placed_however_confident(self):
        self._refresh(0.45)
        rejected = LearningObjectMatchSuggestion.objects.get(evidence__label="solid")
        rejected.status = LearningObjectMatchSuggestion.Status.REJECTED
        rejected.save(update_fields=["status"])

        self._refresh(0.8)

        self.solids_b.refresh_from_db()
        self.diagram_b.refresh_from_db()
        self.solid_a.refresh_from_db()
        self.assertNotEqual(self.solids_b.group_id, self.solid_a.group_id)
        self.assertNotEqual(self.diagram_b.group_id, self.solid_a.group_id)
        rejected.refresh_from_db()
        self.assertEqual(rejected.status, LearningObjectMatchSuggestion.Status.REJECTED)
        self.assertFalse(
            LearningObjectMatchSuggestion.objects.filter(
                status=LearningObjectMatchSuggestion.Status.PENDING,
                evidence__label="solid",
            ).exists()
        )

    def test_a_represented_duplicate_never_joins_a_unit(self):
        self.diagram_b.represented_by = self.solids_b
        self.diagram_b.save(update_fields=["represented_by"])
        original_group_id = self.diagram_b.group_id

        self._refresh(0.8)

        for candidate in heading_unit_candidates(self.topic):
            for side in (candidate["left"], candidate["right"]):
                self.assertNotIn(self.diagram_b.id, [item.id for item in side])
        self.diagram_b.refresh_from_db()
        self.solids_b.refresh_from_db()
        self.solid_a.refresh_from_db()
        self.assertEqual(self.diagram_b.group_id, original_group_id)
        self.assertEqual(self.diagram_b.represented_by_id, self.solids_b.id)
        self.assertNotEqual(self.solids_b.group_id, self.solid_a.group_id)

    def test_a_locked_concept_name_is_never_dissolved_automatically(self):
        group = self.solids_b.group
        group.version_selection = {"label_locked": True}
        group.save(update_fields=["version_selection"])

        self._refresh(0.8)

        self.solids_b.refresh_from_db()
        self.solid_a.refresh_from_db()
        self.assertNotEqual(self.solids_b.group_id, self.solid_a.group_id)
        self.assertTrue(LearningObjectGroup.objects.filter(pk=group.id).exists())
        self.assertTrue(
            LearningObjectMatchSuggestion.objects.filter(
                status=LearningObjectMatchSuggestion.Status.PENDING,
                evidence__label="solid",
            ).exists()
        )

    def test_a_teacher_may_still_accept_the_card_for_a_locked_concept(self):
        group = self.solids_b.group
        group.version_selection = {"label_locked": True}
        group.save(update_fields=["version_selection"])
        self._refresh(0.8)
        suggestion = LearningObjectMatchSuggestion.objects.get(evidence__label="solid")

        response = authenticated_api_client().post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}"
            f"/match-suggestions/{suggestion.id}/accept/",
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.solids_b.refresh_from_db()
        self.diagram_b.refresh_from_db()
        self.solid_a.refresh_from_db()
        self.assertEqual(self.solids_b.group_id, self.solid_a.group_id)
        self.assertEqual(self.diagram_b.group_id, self.solid_a.group_id)

    def test_placing_unpublishes_the_topic_and_the_payload_says_so(self):
        self.topic.published = True
        self.topic.published_at = timezone.now()
        self.topic.save(update_fields=["published", "published_at"])

        counts = self._refresh(0.8)

        self.assertGreaterEqual(counts["placed"], 1)
        self.topic.refresh_from_db()
        self.assertFalse(self.topic.published)
        self.assertIsNone(self.topic.published_at)
        response = authenticated_api_client().get(
            f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}/learning-resources/",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["outline_node"]["published"])

    def test_a_card_alone_leaves_the_topic_published(self):
        self.topic.published = True
        self.topic.published_at = timezone.now()
        self.topic.save(update_fields=["published", "published_at"])

        self._refresh(0.45)

        self.topic.refresh_from_db()
        self.assertTrue(self.topic.published)


class ConnectivityRecheckTests(TestCase):
    """Three PDFs name the same concept: placing the first pair connects the
    run, which disqualifies the second pair in the very same pass."""

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.a, self.b, self.c = (
            LearningMaterial.objects.create(
                course=self.course, outline_node=self.topic, title=name,
                generated_json=dict(confirmed),
            )
            for name in ("A", "B", "C")
        )
        self.a_solid = self._object(self.a, "Solid", "", 0)
        self.b_solid = self._object(self.b, "Solids", "Solids", 0)
        self.b_diagram = self._object(self.b, "Diagram description", "Solids", 1)
        self.c_solid = self._object(self.c, "Solid", "", 0)

    def _object(self, material, title, section, order, kind="text"):
        group = LearningObjectGroup.objects.create(outline_node=self.topic, label=title)
        return LearningObject.objects.create(
            material=material, group=group, title=title, content=f"{title} text.",
            section_title=section, order=order, kind=kind,
        )

    def test_a_placement_disqualifies_a_later_candidate_in_the_same_pass(self):
        runtime = FakeRuntime()
        runtime.pair_scores = lambda pairs: [0.8 for _ in pairs]
        with patch.dict(os.environ, SEMANTIC_ENV):
            counts = refresh_heading_unit_suggestions(self.topic, runtime_instance=runtime)

        self.assertEqual(counts["placed"], 1)
        for item in (self.a_solid, self.b_solid, self.b_diagram, self.c_solid):
            item.refresh_from_db()
        self.assertEqual(self.b_solid.group_id, self.a_solid.group_id)
        self.assertEqual(self.b_diagram.group_id, self.a_solid.group_id)
        self.assertNotEqual(self.c_solid.group_id, self.a_solid.group_id)
