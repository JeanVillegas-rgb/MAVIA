from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)

from .testing import without_measurements
from .models import LessonVariant
from .version_assignment import (
    assign_group_versions,
    assign_source_as_representative,
    assign_source_to_slot,
    bundle_roles,
    clean_group_label,
    set_bundle_role,
    version_bundles,
)


SHORT = "Solid has a fixed shape. It holds its form. It does not flow."
LONG = (
    "A solid is a state of matter that maintains a fixed shape and a fixed volume. "
    "The particles inside it are packed tightly together in a regular arrangement. "
    "Because those particles cannot move past one another, a solid does not flow."
)
MIDDLING = "A solid keeps its shape. The particles are packed closely. It will not flow away."


class VersionAssignmentTests(TestCase):
    def setUp(self):
        without_measurements(self)
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=self.course, title="Matter", order=0, depth=0
        )
        self.group = LearningObjectGroup.objects.create(outline_node=self.node)
        self.now = timezone.now()

    def _material(self, title, minutes_offset):
        material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title=title
        )
        LearningMaterial.objects.filter(pk=material.pk).update(
            created_at=self.now + timedelta(minutes=minutes_offset)
        )
        material.refresh_from_db()
        return material

    def _object(self, material, content, title="Solid"):
        return LearningObject.objects.create(
            material=material, group=self.group, title=title, content=content, order=0
        )

    def test_group_label_cleaner_removes_only_structural_title_markers(self):
        self.assertEqual(clean_group_label("3.1 Matter (Part 1 of 2)"), "Matter")
        self.assertEqual(clean_group_label("Lesson 4: Properties of Gases"), "Properties of Gases")
        self.assertEqual(clean_group_label("3D Shapes"), "3D Shapes")

    def test_singleton_uses_its_cleaned_title_as_group_label(self):
        self._object(
            self._material("PDF one", 0),
            SHORT,
            title="2.1 Solid (Part 1 of 3)",
        )

        assign_group_versions(self.group)

        self.group.refresh_from_db()
        self.assertEqual(self.group.label, "Solid")

    @patch("course.version_assignment.classify_group_versions")
    def test_group_uses_cleaned_first_pdf_standard_title(self, classify):
        first = self._object(
            self._material("PDF one", 0), SHORT, title="1. Solid (Part 1 of 2)"
        )
        second = self._object(
            self._material("PDF two", 5), LONG, title="2. Properties of Solids (Part 2 of 2)"
        )
        classify.return_value = {
            first.id: {"slot": "SIMPLIFIED", "confidence": 0.96, "reason": "Shorter."},
            second.id: {"slot": "ORIGINAL", "confidence": 0.94, "reason": "Balanced."},
        }

        assign_group_versions(self.group, use_llm=True)

        self.group.refresh_from_db()
        self.assertEqual(self.group.label, "Solid")

    @patch("course.version_assignment.classify_group_versions")
    def test_teacher_group_label_is_not_overwritten(self, classify):
        self.group.label = "Teacher's concept name"
        self.group.version_selection = {"label_locked": True}
        self.group.save(update_fields=["label", "version_selection"])
        first = self._object(self._material("PDF one", 0), SHORT, title="Solid Part 1 of 2")
        second = self._object(self._material("PDF two", 5), LONG, title="Solid Part 2 of 2")
        classify.return_value = {
            first.id: {"slot": "ORIGINAL", "confidence": 0.95, "reason": "Balanced."},
            second.id: {"slot": "ELABORATED", "confidence": 0.95, "reason": "Longer."},
        }

        assign_group_versions(self.group, use_llm=True)

        self.group.refresh_from_db()
        self.assertEqual(self.group.label, "Teacher's concept name")

    def test_legacy_custom_group_label_is_inferred_as_teacher_written(self):
        self.group.label = "States of Matter"
        self.group.save(update_fields=["label"])
        self._object(self._material("PDF one", 0), SHORT, title="Solid (Part 1 of 1)")

        assign_group_versions(self.group)

        self.group.refresh_from_db()
        self.assertEqual(self.group.label, "States of Matter")
        self.assertTrue(self.group.version_selection["label_locked"])

    @patch("course.version_assignment.classify_group_versions")
    def test_llm_and_readability_agreement_is_assigned_with_provenance(self, classify):
        first = self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), LONG)
        classify.return_value = {
            first.id: {"slot": "ORIGINAL", "confidence": 0.93, "reason": "Balanced."},
            second.id: {"slot": "ELABORATED", "confidence": 0.94, "reason": "More explanation."}
        }

        result = assign_group_versions(self.group, use_llm=True)

        self.assertEqual(result["representative_id"], first.id)
        self.assertEqual(len(result["assigned"]), 1)
        self.assertEqual(result["needs_confirmation"], [])

        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        self.assertEqual(result["bundle_roles"], {second.material_id: "ELABORATED"})
        self.assertFalse(LessonVariant.objects.filter(learning_object=first).exists())
        self.assertEqual(
            self.group.version_selection["bundle_roles_assigned_by"][str(second.material_id)],
            "llm_validated",
        )
        self.assertTrue(result["classification_complete"])

        # The model result is durable: opening the review payload again does
        # not make another expensive classification request.
        self.group.refresh_from_db()
        refreshed = assign_group_versions(self.group)
        self.assertTrue(refreshed["classification_complete"])
        self.assertEqual(refreshed["representative_id"], first.id)
        classify.assert_called_once()

    # Changed 2026-09-29: Gemma decides; measurements of the two texts check the
    # label (content_measures). Measurements are patched here so these tests
    # check the decision code; MeasuredCheckTests below uses the real encoder.

    def _row(self, slot, confidence=0.9, problems=()):
        return {
            "slot": slot, "confidence": confidence, "reason": "Model reason.",
            "evidence": {"simplifications": [], "additions": [], "problems": list(problems)},
        }

    def _measures(self, facts_kept=True, adds_content=False, easier=True, weakly_covered_sentences=0):
        return patch(
            "course.content_measures.measure_versions",
            return_value={
                "facts_kept": facts_kept, "adds_content": adds_content, "easier": easier,
                "mean_coverage": 0.8, "novel_sentences": int(adds_content), "dale_chall_change": -1.0,
                "weakly_covered_sentences": weakly_covered_sentences,
            },
        )

    def _assign(self, classify, row, **measures):
        first = self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), LONG)
        classify.return_value = {
            first.id: {"slot": "ORIGINAL", "confidence": 0.93, "reason": "Balanced."},
            second.id: row,
        }
        with self._measures(**measures):
            return second, assign_group_versions(self.group, use_llm=True)

    @patch("course.version_assignment.classify_group_versions")
    def test_a_simplified_label_the_measurements_support_is_stored(self, classify):
        second, result = self._assign(classify, self._row("SIMPLIFIED"), adds_content=False, easier=True)

        self.assertEqual(result["bundle_roles"], {second.material_id: "SIMPLIFIED"})

    @patch("course.version_assignment.classify_group_versions")
    def test_simplified_that_keeps_the_facts_and_adds_content_needs_review(self, classify):
        """A novel sentence is not proof that the text is Elaborated."""
        second, result = self._assign(classify, self._row("SIMPLIFIED"), adds_content=True, easier=False)

        self.assertEqual(result["bundle_roles"], {})
        reviewed = result["needs_confirmation"][0]
        self.assertEqual(reviewed["llm_slot"], "SIMPLIFIED")
        self.assertIn("simplified_but_adds_content", reviewed["review_issues"])
        self.assertIn("simplified_but_not_easier", reviewed["review_issues"])

    @patch("course.version_assignment.classify_group_versions")
    def test_old_automatic_role_is_hidden_until_reclassified(self, classify):
        second, _ = self._assign(classify, self._row("ELABORATED"), adds_content=True)
        self.group.refresh_from_db()
        self.assertEqual(bundle_roles(self.group), {second.material_id: "ELABORATED"})

        selection = dict(self.group.version_selection)
        selection["roles_signature"] = "classified-under-old-rules"
        self.group.version_selection = selection
        self.group.save(update_fields=["version_selection"])

        self.assertEqual(bundle_roles(self.group), {})
        self.assertFalse(assign_group_versions(self.group)["classification_complete"])

        set_bundle_role(self.group, second.material_id, "ELABORATED")
        self.assertEqual(bundle_roles(self.group), {second.material_id: "ELABORATED"})

    @patch("course.version_assignment.classify_group_versions")
    def test_simplified_that_adds_content_but_drops_facts_is_not_stored(self, classify):
        second, result = self._assign(classify, self._row("SIMPLIFIED"), facts_kept=False, adds_content=True)

        self.assertEqual(result["bundle_roles"], {})
        self.assertIn("facts_not_kept", result["needs_confirmation"][0]["review_issues"])

    @patch("course.version_assignment.classify_group_versions")
    def test_a_weakly_matched_standard_idea_needs_teacher_review(self, classify):
        second, result = self._assign(
            classify, self._row("ELABORATED"), facts_kept=False,
            adds_content=True, weakly_covered_sentences=1,
        )

        self.assertEqual(result["bundle_roles"], {})
        self.assertEqual(result["needs_confirmation"][0]["material_id"], second.material_id)
        self.assertEqual(result["needs_confirmation"][0]["review_measures"]["weakly_covered_sentences"], 1)

    @patch("course.version_assignment.classify_group_versions")
    def test_a_reported_problem_is_never_corrected_into_a_role(self, classify):
        second, result = self._assign(
            classify, self._row("SIMPLIFIED", problems=["contradicts the original"]), adds_content=True,
        )

        self.assertEqual(result["bundle_roles"], {})

    @patch("course.version_assignment.classify_group_versions")
    def test_nothing_is_ever_corrected_into_simplified(self, classify):
        second, result = self._assign(classify, self._row("ELABORATED"), adds_content=False, easier=True)

        self.assertEqual(result["bundle_roles"], {})
        self.assertEqual(result["needs_confirmation"][0]["review_issues"], ["elaborated_but_adds_nothing"])

    @patch("course.version_assignment.classify_group_versions")
    def test_simplified_that_is_not_easier_is_not_stored(self, classify):
        second, result = self._assign(classify, self._row("SIMPLIFIED"), easier=False)

        self.assertEqual(result["bundle_roles"], {})
        self.assertEqual(result["needs_confirmation"][0]["review_issues"], ["simplified_but_not_easier"])

    @patch("course.version_assignment.classify_group_versions")
    def test_an_elaborated_label_the_measurements_support_is_stored(self, classify):
        second, result = self._assign(classify, self._row("ELABORATED"), adds_content=True, easier=False)

        self.assertEqual(result["bundle_roles"], {second.material_id: "ELABORATED"})

    @patch("course.version_assignment.classify_group_versions")
    def test_elaborated_that_adds_nothing_is_not_stored(self, classify):
        second, result = self._assign(classify, self._row("ELABORATED"), adds_content=False)

        self.assertEqual(result["bundle_roles"], {})
        self.assertEqual(result["needs_confirmation"][0]["review_issues"], ["elaborated_but_adds_nothing"])

    @patch("course.version_assignment.classify_group_versions")
    def test_a_text_that_does_not_keep_the_facts_is_not_stored(self, classify):
        second, result = self._assign(classify, self._row("ELABORATED"), facts_kept=False, adds_content=True)

        self.assertEqual(result["bundle_roles"], {})
        self.assertIn("facts_not_kept", result["needs_confirmation"][0]["review_issues"])

    @patch("course.version_assignment.classify_group_versions")
    def test_a_problem_gemma_reports_is_not_stored(self, classify):
        second, result = self._assign(
            classify, self._row("SIMPLIFIED", problems=["says solids change shape"]),
        )

        self.assertEqual(result["bundle_roles"], {})
        self.assertIn("gemma_reports_a_problem", result["needs_confirmation"][0]["review_issues"])

    @patch("course.version_assignment.classify_group_versions")
    def test_model_can_request_review_without_a_version_role(self, classify):
        second, result = self._assign(
            classify, self._row("NEEDS_REVIEW", problems=["drops the volume fact"]), facts_kept=False,
        )

        self.assertEqual(result["bundle_roles"], {})
        self.assertEqual(result["needs_confirmation"][0]["material_id"], second.material_id)

    @patch("course.version_assignment.classify_group_versions")
    def test_fkgl_and_low_confidence_are_concerns_not_decisions(self, classify):
        # LONG is clearly longer and harder than SHORT by FKGL, and 0.3 is low:
        # neither stops a label the measurements support.
        second, result = self._assign(classify, self._row("SIMPLIFIED", confidence=0.3))

        self.assertEqual(result["bundle_roles"], {second.material_id: "SIMPLIFIED"})
        concerns = result["assigned"][0]["review_concerns"]
        self.assertIn("fkgl_points_the_other_way", concerns)
        self.assertIn("low_self_reported_confidence", concerns)

    @patch("course.version_assignment.classify_group_versions")
    def test_without_the_encoder_the_label_is_kept_with_a_concern(self, classify):
        from .content_measures import MeasurementUnavailable

        first = self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), LONG)
        classify.return_value = {
            first.id: {"slot": "ORIGINAL", "confidence": 0.93, "reason": "Balanced."},
            second.id: self._row("ELABORATED"),
        }
        with patch("course.content_measures.measure_versions", side_effect=MeasurementUnavailable("no model")):
            result = assign_group_versions(self.group, use_llm=True)

        self.assertEqual(result["bundle_roles"], {second.material_id: "ELABORATED"})
        self.assertIn("measurements_unavailable", result["assigned"][0]["review_concerns"])

    @patch("course.version_assignment.classify_group_versions")
    def test_a_caught_role_stays_caught_on_the_next_run(self, classify):
        second, _first_run = self._assign(classify, self._row("ELABORATED"), adds_content=False)
        self.group.refresh_from_db()

        with self._measures(adds_content=False):
            again = assign_group_versions(self.group, use_llm=True)

        self.assertEqual(again["bundle_roles"], {})
        self.assertEqual(len(again["needs_confirmation"]), 1)
        self.assertEqual(classify.call_count, 1)

    @patch("course.version_assignment.classify_group_versions")
    def test_reading_a_concept_again_reuses_the_role_check(self, classify):
        """Every page load reads the concept; re-measuring there loaded the
        grouping models and logged on screens that have nothing to do with versions."""
        second, _first_run = self._assign(classify, self._row("ELABORATED"), adds_content=True)
        self.group.refresh_from_db()

        with self._measures(adds_content=True) as measure:
            again = assign_group_versions(self.group)

        measure.assert_not_called()
        self.assertEqual(again["bundle_roles"], {second.material_id: "ELABORATED"})

    @patch("course.version_assignment.classify_group_versions")
    def test_a_changed_text_is_checked_again(self, classify):
        second, _first_run = self._assign(classify, self._row("ELABORATED"), adds_content=True)
        second.content += " Particles also move faster when heated."
        second.save()
        self.group.refresh_from_db()

        # A changed text voids the stored answer; the next sort asks again and
        # the stored check is not reused for text it never measured.
        with self._measures(adds_content=True) as measure:
            assign_group_versions(self.group, use_llm=True)

        measure.assert_called()

    @patch("course.version_assignment.classify_group_versions")
    def test_first_relevant_pdf_stays_standard_even_if_llm_prefers_second(self, classify):
        first = self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), LONG)
        classify.return_value = {
            first.id: {"slot": "SIMPLIFIED", "confidence": 0.96, "reason": "Clearer."},
            second.id: {"slot": "ORIGINAL", "confidence": 0.94, "reason": "Balanced."},
        }

        result = assign_group_versions(self.group, use_llm=True)

        self.assertEqual(result["representative_id"], first.id)
        self.assertEqual(result["standard_material_id"], first.material_id)
        classify.assert_called_once()
        self.assertEqual(classify.call_args.kwargs["representative"].id, first.id)

    @patch("course.version_assignment.classify_group_versions")
    def test_llm_review_decision_does_not_create_a_version(self, classify):
        first = self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), MIDDLING)
        classify.return_value = {
            first.id: {"slot": "ORIGINAL", "confidence": 0.91, "reason": "Baseline."},
            second.id: {"slot": "NEEDS_REVIEW", "confidence": 0.72, "reason": "Equivalent wording."},
        }

        result = assign_group_versions(self.group, use_llm=True)

        self.assertEqual(len(result["needs_confirmation"]), 1)
        self.assertEqual(result["bundle_roles"], {})

    def test_thin_margin_is_routed_to_the_teacher_for_confirmation(self):
        first = self._object(self._material("PDF one", 0), SHORT)
        self._object(self._material("PDF two", 5), MIDDLING)

        result = assign_group_versions(self.group)

        self.assertEqual(result["assigned"], [])
        self.assertEqual(len(result["needs_confirmation"]), 1)
        self.assertFalse(result["needs_confirmation"][0]["confident"])
        self.assertFalse(result["classification_complete"])
        self.assertFalse(LessonVariant.objects.filter(learning_object=first).exists())

    @patch("course.version_assignment.classify_group_versions")
    def test_a_bundle_the_model_skipped_is_asked_about_again(self, classify):
        """A run is only complete when every bundle was actually ruled on.

        Recording a partial run as complete would turn the readability guess
        for the bundle Gemma ignored into a settled decision, and the teacher
        would never be asked about it again.
        """
        first = self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), LONG)
        third = self._object(self._material("PDF three", 10), MIDDLING)
        classify.return_value = {
            first.id: {"slot": "ORIGINAL", "confidence": 0.93, "reason": "Balanced."},
            second.id: {"slot": "ELABORATED", "confidence": 0.94, "reason": "Fuller."},
        }

        result = assign_group_versions(self.group, use_llm=True)

        self.assertFalse(result["classification_complete"])
        self.assertEqual(
            [entry["material_id"] for entry in result["needs_confirmation"]],
            [third.material_id],
        )

        self.group.refresh_from_db()
        again = assign_group_versions(self.group)

        self.assertEqual(
            [entry["material_id"] for entry in again["needs_confirmation"]],
            [third.material_id],
        )
        # The bundle Gemma did rule on keeps its role.
        self.assertEqual(again["bundle_roles"][second.material_id], "ELABORATED")

    @patch("course.version_assignment.classify_group_versions")
    def test_a_displaced_automatic_role_keeps_its_own_provenance(self, classify):
        """Displacement is not a teacher's ruling about the bundle displaced.

        Stamping it as the teacher's would freeze a readability guess the
        teacher never saw, and no later classification could correct it.
        """
        first = self._object(self._material("PDF one", 0), LONG)
        second = self._object(self._material("PDF two", 5), SHORT)
        third = self._object(self._material("PDF three", 10), MIDDLING)
        # Changed 2026-09-21: a read no longer records a role, so the
        # automatic role this test displaces is put on the record the way
        # production puts it there -- by a classification run. Gemma skipped
        # the third bundle, so readability still owns that one.
        classify.return_value = {
            first.id: {"slot": "ORIGINAL", "confidence": 0.9, "reason": "Baseline."},
            second.id: {"slot": "SIMPLIFIED", "confidence": 0.9, "reason": "Plainer."},
            third.id: {"slot": "NEEDS_REVIEW", "confidence": 0.9, "reason": "Alternative."},
        }
        assign_group_versions(self.group, use_llm=True)
        self.group.refresh_from_db()
        self.assertEqual(
            self.group.version_selection["bundle_roles_assigned_by"][str(second.material_id)],
            "llm_validated",
        )

        assign_source_to_slot(first, third, "SIMPLIFIED")

        self.group.refresh_from_db()
        provenance = self.group.version_selection["bundle_roles_assigned_by"]
        self.assertEqual(provenance[str(third.material_id)], "teacher")
        # The displaced bundle keeps the provenance it already had, which is
        # the point: it is not restamped as something the teacher ruled on.
        self.assertEqual(provenance[str(second.material_id)], "displaced_by_teacher")

        # The displaced source remains open for an explicit teacher decision.
        second.content = (
            "A solid keeps one shape and one volume. Its particles are locked into a "
            "repeating lattice, so they vibrate in place instead of moving past one another."
        )
        second.save(update_fields=["content"])
        classify.return_value = {
            first.id: {"slot": "ORIGINAL", "confidence": 0.9, "reason": "Baseline."},
            second.id: {"slot": "ELABORATED", "confidence": 0.9, "reason": "Fuller."},
            third.id: {"slot": "NEEDS_REVIEW", "confidence": 0.9, "reason": "Alternative."},
        }
        outcome = assign_group_versions(self.group, use_llm=True)

        self.assertEqual(
            outcome["bundle_roles"],
            {third.material_id: "SIMPLIFIED"},
        )
        self.assertIn(second.material_id, [item["material_id"] for item in outcome["needs_confirmation"]])

    def test_the_newest_teacher_decision_wins_a_contested_slot(self):
        """Two teacher rulings cannot both hold one slot.

        The later ruling is the teacher's current intent; the earlier one is
        recorded as displaced rather than as a role they chose.
        """
        first = self._object(self._material("PDF one", 0), LONG)
        second = self._object(self._material("PDF two", 5), SHORT)
        third = self._object(self._material("PDF three", 10), MIDDLING)

        assign_source_to_slot(first, second, "SIMPLIFIED")
        assign_source_to_slot(first, third, "SIMPLIFIED")

        self.group.refresh_from_db()
        roles = self.group.version_selection["bundle_roles"]
        provenance = self.group.version_selection["bundle_roles_assigned_by"]
        self.assertEqual(roles[str(third.material_id)], "SIMPLIFIED")
        self.assertEqual(provenance[str(third.material_id)], "teacher")
        self.assertNotIn(str(second.material_id), roles)
        self.assertEqual(provenance[str(second.material_id)], "displaced_by_teacher")

    def test_two_teacher_roles_for_one_slot_are_settled_by_recency(self):
        first = self._object(self._material("PDF one", 0), LONG)
        second = self._object(self._material("PDF two", 5), SHORT)
        third = self._object(self._material("PDF three", 10), MIDDLING)
        # Written straight into the record, so nothing displaced either one.
        set_bundle_role(self.group, second.material_id, "SIMPLIFIED")
        set_bundle_role(self.group, third.material_id, "SIMPLIFIED")

        outcome = assign_group_versions(self.group)

        self.assertEqual(outcome["bundle_roles"][third.material_id], "SIMPLIFIED")
        self.assertNotIn(second.material_id, outcome["bundle_roles"])
        # Changed 2026-09-21: the resolution is re-derived on every call and
        # reported, but a read writes nothing, so the record still holds what
        # the two writes put there. Production cannot reach this state anyway:
        # `assign_source_to_slot` is the only path a teacher role takes, and
        # it displaces the losing claim as it stores the winning one.
        self.assertEqual(
            outcome["needs_confirmation"][0]["material_id"], second.material_id,
        )
        self.group.refresh_from_db()
        self.assertEqual(
            self.group.version_selection["bundle_roles_assigned_by"][str(second.material_id)],
            "teacher",
        )
        self.assertEqual(first.id, outcome["representative_id"])

    def test_a_teacher_label_matching_a_section_heading_is_kept(self):
        """A teacher who names a concept after its heading is still a teacher.

        The label this function writes is recorded, so anything else in the
        field was typed by somebody and is never overwritten.
        """
        LearningObject.objects.create(
            material=self._material("PDF one", 0), group=self.group,
            title="Solid", section_title="States of Matter", content=SHORT, order=0,
        )
        self.group.label = "States of Matter"
        self.group.save(update_fields=["label"])

        assign_group_versions(self.group)

        self.group.refresh_from_db()
        self.assertEqual(self.group.label, "States of Matter")
        self.assertTrue(self.group.version_selection["label_locked"])

    def test_a_lone_object_is_named_after_itself_not_its_shared_heading(self):
        """Design 3.5. Solid, Liquid and Gas all sit under "Matter"."""
        self._object(self._material("PDF one", 0), SHORT, title="3. Solid")
        LearningObject.objects.filter(group=self.group).update(
            section_title="Matter",
        )

        assign_group_versions(self.group)

        self.group.refresh_from_db()
        self.assertEqual(self.group.label, "Solid")
        self.assertEqual(self.group.version_selection["auto_label"], "Solid")

    def test_a_bundle_of_two_is_still_named_after_its_heading(self):
        material = self._material("PDF one", 0)
        LearningObject.objects.create(
            material=material, group=self.group, title="Figure 3",
            content=SHORT, order=0,
        )
        LearningObject.objects.create(
            material=material, group=self.group, title="Shape",
            section_title="Comparing the Three States", content=LONG, order=1,
        )

        assign_group_versions(self.group)

        self.group.refresh_from_db()
        self.assertEqual(self.group.label, "Comparing the Three States")

    def test_a_locked_label_survives_the_single_object_rule(self):
        self.group.label = "Teacher's concept name"
        self.group.version_selection = {"label_locked": True}
        self.group.save(update_fields=["label", "version_selection"])
        self._object(self._material("PDF one", 0), SHORT, title="Solid")
        LearningObject.objects.filter(group=self.group).update(section_title="Matter")

        assign_group_versions(self.group)

        self.group.refresh_from_db()
        self.assertEqual(self.group.label, "Teacher's concept name")

    def test_a_label_this_module_wrote_is_still_followed(self):
        self._object(self._material("PDF one", 0), SHORT, title="Solid")

        assign_group_versions(self.group)

        self.group.refresh_from_db()
        self.assertEqual(self.group.label, "Solid")
        self.assertEqual(self.group.version_selection["auto_label"], "Solid")
        self.assertFalse(self.group.version_selection.get("label_locked"))

    @patch("course.version_assignment.classify_group_versions")
    def test_slot_collision_keeps_the_larger_margin_and_reviews_the_other(self, classify):
        first = self._object(self._material("PDF one", 0), SHORT)
        bigger = self._object(self._material("PDF two", 5), LONG)
        # Also confidently "elaborated", but by a narrower Flesch-Kincaid
        # margin than LONG, so it loses the slot and needs review.
        smaller = self._object(
            self._material("PDF three", 10),
            "A solid keeps a fixed shape at all times. The particles inside it are packed "
            "together very closely. It cannot flow the way that water does.",
        )

        classify.return_value = {
            first.id: {"slot": "ORIGINAL", "confidence": 0.93, "reason": "Balanced."},
            bigger.id: {"slot": "ELABORATED", "confidence": 0.96, "reason": "Fuller."},
            smaller.id: {"slot": "ELABORATED", "confidence": 0.91, "reason": "Also fuller."},
        }
        result = assign_group_versions(self.group, use_llm=True)

        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        self.assertEqual(
            result["bundle_roles"],
            {bigger.material_id: "ELABORATED"},
        )
        self.assertIn(smaller.material_id, [item["material_id"] for item in result["needs_confirmation"]])

    def test_singleton_group_assigns_nothing(self):
        self._object(self._material("PDF one", 0), SHORT)
        result = assign_group_versions(self.group)
        self.assertEqual(result["assigned"], [])
        self.assertEqual(result["needs_confirmation"], [])

    @patch("course.version_assignment.classify_group_versions")
    def test_reassignment_is_idempotent(self, classify):
        first = self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), LONG)
        classify.return_value = {
            first.id: {"slot": "ORIGINAL", "confidence": 0.93, "reason": "Balanced."},
            second.id: {"slot": "ELABORATED", "confidence": 0.95, "reason": "Fuller."}
        }

        assign_group_versions(self.group, use_llm=True)
        self.group.refresh_from_db()
        outcome = assign_group_versions(self.group, use_llm=True)

        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        self.assertEqual(outcome["bundle_roles"], {second.material_id: "ELABORATED"})
        self.assertFalse(LessonVariant.objects.exists())

    def test_saved_teacher_decision_is_not_returned_for_confirmation_again(self):
        self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), MIDDLING)
        # Changed 2026-09-20: roles are per bundle; a PDF-supplied version is its own objects.
        set_bundle_role(self.group, second.material_id, "SIMPLIFIED")

        result = assign_group_versions(self.group)

        self.assertEqual(result["needs_confirmation"], [])
        self.assertEqual(result["assigned"][0]["learning_object_id"], second.id)
        self.assertTrue(result["assigned"][0]["persisted"])

    @patch("course.version_assignment.classify_group_versions")
    def test_adding_a_second_pdf_does_not_replace_the_existing_standard(self, classify):
        first = self._object(self._material("First PDF", 0), SHORT)
        LessonVariant.objects.create(learning_object=first, variant="SIMPLIFIED", narration="Previously generated.")
        second = self._object(self._material("New PDF", 5), MIDDLING)
        classify.return_value = {
            second.id: {"slot": "NEEDS_REVIEW", "confidence": 0.9, "reason": "Alternative."},
        }
        result = assign_group_versions(self.group, use_llm=True)
        self.assertEqual(result["representative_id"], first.id)
        self.assertTrue(LessonVariant.objects.filter(learning_object=first).exists())
        self.assertEqual(assign_group_versions(self.group)["representative_id"], first.id)


class BundleRoleTests(TestCase):
    """Roles are decided per bundle, not per object.

    One PDF teaches Solid as a section, its diagram and its examples; the other
    as a single passage. The whole bundle takes one role.
    """

    def setUp(self):
        without_measurements(self)
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.first = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A", generated_json=dict(confirmed))
        self.second = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="B", generated_json=dict(confirmed))
        self.group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solid")
        self.standard = LearningObject.objects.create(
            material=self.first, group=self.group, title="Solid", order=0,
            content=(
                "A solid has a definite shape and a definite volume because its constituent "
                "particles occupy fixed positions within a rigid lattice arrangement."
            ),
        )
        self.simple_lead = LearningObject.objects.create(
            material=self.second, group=self.group, title="Solids", order=0, section_title="Solids",
            content="In a solid, bits are packed tight. They stay in place.",
        )
        self.simple_tail = LearningObject.objects.create(
            material=self.second, group=self.group, title="Everyday examples", order=1,
            section_title="Solids", content="Ice cubes. A rock. A book.",
        )

    def test_the_whole_bundle_takes_one_role(self):
        outcome = assign_group_versions(self.group)

        self.assertEqual(outcome["standard_material_id"], self.first.id)
        self.assertEqual(outcome["representative_id"], self.standard.id)
        self.assertEqual(outcome["bundle_roles"], {self.second.id: "SIMPLIFIED"})

    @patch("course.version_assignment.classify_group_versions")
    def test_a_pdf_supplied_version_stores_no_copied_text(self, classify):
        # Changed 2026-09-21: a plain read proposes a role but no longer
        # records one, so the supplied version exists only once the concept
        # has actually been classified -- which is how the teacher gets here.
        classify.return_value = {
            self.standard.id: {"slot": "ORIGINAL", "confidence": 0.95, "reason": "Baseline."},
            self.simple_lead.id: {"slot": "SIMPLIFIED", "confidence": 0.9, "reason": "Plainer."},
        }
        assign_group_versions(self.group, use_llm=True)

        self.assertFalse(
            LessonVariant.objects.filter(origin=LessonVariant.Origin.SOURCE_PDF).exists()
        )
        self.assertEqual(
            version_bundles(self.group)["SIMPLIFIED"], [self.simple_lead, self.simple_tail],
        )

    def test_a_teacher_role_change_survives_a_refresh(self):
        assign_group_versions(self.group)

        set_bundle_role(self.group, self.second.id, "ELABORATED")
        outcome = assign_group_versions(self.group)

        self.assertEqual(outcome["bundle_roles"], {self.second.id: "ELABORATED"})

    @patch("course.version_assignment.review_gemma_role")
    @patch("course.version_assignment.classify_group_versions")
    def test_recommended_pdf_becoming_standard_swaps_roles_without_reclassification(
        self, classify, review,
    ):
        classify.return_value = {
            self.standard.id: {"slot": "ORIGINAL", "confidence": 0.95, "reason": "Baseline."},
            self.simple_lead.id: {
                "slot": "SIMPLIFIED", "confidence": 0.55, "reason": "Likely simpler."
            },
        }
        review.return_value = {
            "accepted": False,
            "slot": "SIMPLIFIED",
            "issues": ["Teacher should decide."],
            "concerns": [],
            "measures": {},
        }
        initial = assign_group_versions(self.group, use_llm=True)
        self.assertTrue(initial["classification_complete"])
        self.assertEqual(initial["needs_confirmation"][0]["material_id"], self.second.id)
        recommended_slot = initial["needs_confirmation"][0]["llm_slot"]

        assign_source_as_representative(self.group, self.simple_lead)

        self.group.refresh_from_db()
        self.standard.refresh_from_db()
        self.simple_lead.refresh_from_db()
        self.assertEqual(self.group.version_selection["standard_material_id"], self.second.id)
        self.assertEqual(
            self.group.version_selection["bundle_roles"],
            {str(self.first.id): recommended_slot},
        )
        self.assertEqual(self.standard.represented_by_id, self.simple_lead.id)
        self.assertIsNone(self.simple_lead.represented_by_id)
        outcome = assign_group_versions(self.group)
        self.assertTrue(outcome["classification_complete"])
        self.assertEqual(outcome["needs_confirmation"], [])

    def test_the_standard_bundle_is_reported_in_document_order(self):
        extra = LearningObject.objects.create(
            material=self.first, group=self.group, title="Particle diagram", order=1,
            section_title="Solid", content="Particles sit in a grid.",
        )

        self.assertEqual(version_bundles(self.group)["STANDARD"], [self.standard, extra])


class ReadDoesNotDecideRolesTests(TestCase):
    """Reading a concept must not record a role for it.

    Step 1 of the review is about grouping: which objects teach the same thing.
    It loads the topic, and every load used to run the readability comparison
    and *save* what it proposed, so a concept came back already carrying
    Simplified/Elaborated before the teacher reached the classification step
    and before Gemma had seen it. The proposal is still returned -- callers
    that want to show it can -- but only a real classification run, or a
    teacher, writes a role down.
    """

    def setUp(self):
        without_measurements(self)
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.node = OutlineNode.objects.create(
            course=self.course, title="Matter", order=0, depth=0
        )
        self.group = LearningObjectGroup.objects.create(outline_node=self.node)
        self.now = timezone.now()

    def _material(self, title, minutes_offset):
        material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title=title
        )
        LearningMaterial.objects.filter(pk=material.pk).update(
            created_at=self.now + timedelta(minutes=minutes_offset)
        )
        material.refresh_from_db()
        return material

    def _object(self, material, content):
        return LearningObject.objects.create(
            material=material, group=self.group, title="Solid", content=content, order=0
        )

    def test_a_read_proposes_a_role_without_storing_it(self):
        self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), LONG)

        outcome = assign_group_versions(self.group)

        # The proposal is still available to whoever asked for it.
        self.assertEqual(outcome["bundle_roles"], {second.material_id: "ELABORATED"})
        # Nothing was decided, so nothing is written down.
        self.group.refresh_from_db()
        self.assertNotIn("bundle_roles", self.group.version_selection or {})
        self.assertNotIn("bundle_roles_assigned_by", self.group.version_selection or {})

    def test_a_read_never_stores_a_role_however_many_times_it_runs(self):
        self._object(self._material("PDF one", 0), SHORT)
        self._object(self._material("PDF two", 5), LONG)

        for _ in range(3):
            assign_group_versions(self.group)

        self.group.refresh_from_db()
        self.assertFalse((self.group.version_selection or {}).get("bundle_roles"))

    @patch("course.version_assignment.classify_group_versions")
    def test_a_classification_run_still_writes_the_role_down(self, classify):
        first = self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), LONG)
        classify.return_value = {
            first.id: {"slot": "ORIGINAL", "confidence": 0.93, "reason": "Balanced."},
            second.id: {"slot": "ELABORATED", "confidence": 0.95, "reason": "Fuller."},
        }

        assign_group_versions(self.group, use_llm=True)

        self.group.refresh_from_db()
        self.assertEqual(
            self.group.version_selection["bundle_roles"],
            {str(second.material_id): "ELABORATED"},
        )

    def test_a_teacher_role_is_untouched_by_a_read(self):
        self._object(self._material("PDF one", 0), SHORT)
        second = self._object(self._material("PDF two", 5), LONG)
        set_bundle_role(self.group, second.material_id, "SIMPLIFIED")

        assign_group_versions(self.group)

        self.group.refresh_from_db()
        self.assertEqual(
            self.group.version_selection["bundle_roles"],
            {str(second.material_id): "SIMPLIFIED"},
        )


class MeasuredCheckTests(TestCase):
    """The measurements themselves, with the real encoder grouping uses.

    These show what the measurements see in each case. They verify this code,
    not how accurately Gemma classifies.
    """

    STANDARD = "Solids have a fixed shape and volume."

    def measure(self, candidate, standard=None):
        from .content_measures import measure_versions

        return measure_versions(standard or self.STANDARD, candidate)

    def test_a_simplification_keeps_the_facts_adds_nothing_and_is_easier(self):
        result = self.measure("A solid keeps its shape and takes up a fixed amount of space.")

        self.assertTrue(result["facts_kept"])
        self.assertFalse(result["adds_content"])
        self.assertTrue(result["easier"])

    def test_an_elaboration_adds_content(self):
        result = self.measure(
            "Solids have a fixed shape and volume. Their particles remain close together and "
            "vibrate around fixed positions. A stone, for example, keeps its shape when moved "
            "between containers."
        )

        self.assertTrue(result["facts_kept"])
        self.assertTrue(result["adds_content"])

    def test_an_unrelated_text_does_not_keep_the_facts(self):
        result = self.measure("Plants make their own food from sunlight in a process called photosynthesis.")

        self.assertFalse(result["facts_kept"])

    def test_one_omitted_idea_cannot_hide_behind_a_good_average(self):
        import numpy as np

        from .content_measures import measure_versions

        standard = "Matter has mass. Matter takes up space."
        candidate = "Matter has mass. Objects have weight."
        with patch("course.content_measures._similarities", return_value=np.array([
            [0.95, 0.45],
            [0.31, 0.30],
        ])):
            result = measure_versions(standard, candidate)

        self.assertGreater(result["mean_coverage"], 0.55)
        self.assertEqual(result["weakly_covered_sentences"], 1)
        self.assertFalse(result["facts_kept"])

    def test_short_but_unfamiliar_words_are_not_easier(self):
        """FKGL read this as easy; Dale-Chall counts the unfamiliar words."""
        result = self.measure(
            "Solids resist deformation: intermolecular forces lock particles into a lattice.",
            standard="A solid keeps its shape because its particles are held tightly in place and can only vibrate.",
        )

        self.assertFalse(result["easier"])

    def test_dale_chall_scores_familiar_words_lower(self):
        from .content_measures import dale_chall

        self.assertLess(dale_chall("The water is cold."), dale_chall("Intermolecular deformation persists."))


class DaleChallWordFormTests(TestCase):
    def test_plurals_and_past_tense_of_familiar_words_are_familiar(self):
        from .content_measures import dale_chall

        self.assertEqual(dale_chall("The cold stones melted."), dale_chall("The cold stone melt."))
