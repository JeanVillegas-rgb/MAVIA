"""What survives when the first object of a concept's Normal PDF is deleted."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.db import connection
from django.test import TestCase

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)
from lessons.tests import authenticated_api_client

from .models import LessonVariant
from .testing import without_measurements
from .variant_generator import fill_missing_bundle_slots
from .version_assignment import (
    assign_group_versions,
    assign_source_as_representative,
    release_from_group,
    set_bundle_role,
    settle_group,
    version_bundles,
)


class NormalSourceFixture:
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="Matter")
        self.group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solids")

    def add_source(self, name, *, order=0):
        material = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title=name,
            generated_json={"learning_objects_confirmed": True},
        )
        obj = LearningObject.objects.create(
            material=material, group=self.group, title="Solids", order=order,
            content=f"{name} explains the shape of a solid.",
        )
        return material, obj

    def select_normal(self, material):
        self.group.version_selection = {"normal_material_id": material.id}
        self.group.save(update_fields=["version_selection"])

    def add_generated_versions(self, obj):
        for slot in ("SIMPLIFIED", "ELABORATED"):
            LessonVariant.objects.create(
                learning_object=obj, variant=slot,
                narration=f"AI-written {slot.lower()} explanation.",
                origin=LessonVariant.Origin.GENERATED,
            )


class NormalObjectDeletionTests(NormalSourceFixture, TestCase):
    def test_only_normal_source_deleted_leaves_no_versions(self):
        a, a_first = self.add_source("PDF A")
        self.select_normal(a)
        self.add_generated_versions(a_first)

        a_first.delete()

        self.assertFalse(self.group.learning_objects.exists())
        self.assertFalse(LessonVariant.objects.exists())
        self.assertEqual(version_bundles(self.group), {})

    def test_simplified_pdf_survives_but_requires_replacement_confirmation(self):
        a, a_first = self.add_source("PDF A")
        b, b_first = self.add_source("PDF B")
        self.select_normal(a)
        set_bundle_role(self.group, b.id, "SIMPLIFIED")
        self.add_generated_versions(a_first)

        a_first.delete()

        self.assertTrue(LearningObject.objects.filter(pk=b_first.pk).exists())
        self.assertFalse(LessonVariant.objects.exists())
        self.assertEqual(version_bundles(self.group), {})
        state = assign_group_versions(self.group)
        self.assertTrue(state["normal_replacement_needed"])
        self.assertFalse(state["original_selected"])
        self.assertIsNone(state["representative_id"])
        self.assertIn("replacement Normal", fill_missing_bundle_slots(self.group)["errors"][0]["detail"])
        self.assertIn("replacement Normal", settle_group(self.group)["errors"][0]["detail"])

        assign_source_as_representative(self.group, b_first)
        self.group.refresh_from_db()
        self.assertFalse(assign_group_versions(self.group)["normal_replacement_needed"])
        self.assertEqual(self.group.version_selection["normal_material_id"], b.id)
        self.assertEqual(self.group.version_selection["normal_assigned_by"], "teacher")

    def test_two_other_pdfs_survive_but_old_roles_are_withheld(self):
        a, a_first = self.add_source("PDF A")
        b, b_first = self.add_source("PDF B")
        c, c_first = self.add_source("PDF C")
        self.select_normal(a)
        set_bundle_role(self.group, b.id, "SIMPLIFIED")
        set_bundle_role(self.group, c.id, "ELABORATED")
        self.add_generated_versions(a_first)

        a_first.delete()

        self.assertTrue(LearningObject.objects.filter(pk=b_first.pk).exists())
        self.assertTrue(LearningObject.objects.filter(pk=c_first.pk).exists())
        self.assertFalse(LessonVariant.objects.exists())
        self.assertEqual(
            version_bundles(self.group),
            {},
        )

        assign_source_as_representative(self.group, c_first)
        self.assertEqual(version_bundles(self.group), {"NORMAL": [c_first]})
        self.assertTrue(LearningObject.objects.filter(pk=b_first.pk).exists())

    def test_next_object_of_same_pdf_becomes_normal_before_another_pdf(self):
        a, a_first = self.add_source("PDF A")
        a_next = LearningObject.objects.create(
            material=a, group=self.group, title="More about solids", order=1,
            content="PDF A continues its explanation of solids.",
        )
        b, b_first = self.add_source("PDF B")
        self.select_normal(a)
        set_bundle_role(self.group, b.id, "SIMPLIFIED")
        self.add_generated_versions(a_first)

        a_first.delete()

        self.assertTrue(LearningObject.objects.filter(pk=a_next.pk).exists())
        self.assertTrue(LearningObject.objects.filter(pk=b_first.pk).exists())
        self.assertFalse(LessonVariant.objects.exists())
        self.assertEqual(
            version_bundles(self.group),
            {"NORMAL": [a_next], "SIMPLIFIED": [b_first]},
        )

    def test_first_pdf_without_this_concept_is_not_its_normal(self):
        LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="PDF A",
        )
        _b, b_first = self.add_source("PDF B")
        self.add_source("PDF C")

        self.assertEqual(version_bundles(self.group)["NORMAL"], [b_first])

    def test_one_pdf_baseline_is_recorded_before_classification(self):
        a, a_first = self.add_source("PDF A")
        assign_group_versions(self.group)
        self.group.refresh_from_db()
        self.assertEqual(self.group.version_selection["normal_material_id"], a.id)
        self.assertEqual(self.group.version_selection["normal_assigned_by"], "upload_order")
        self.add_source("PDF B")

        a_first.delete()

        self.assertTrue(assign_group_versions(self.group)["normal_replacement_needed"])

    def test_existing_llm_chosen_normal_switches_to_first_relevant_pdf(self):
        without_measurements(self)
        a, a_first = self.add_source("PDF A")
        b, b_first = self.add_source("PDF B")
        # Existing courses have no Normal provenance: a prior LLM may have
        # selected B. Its roles must not be reused against the new baseline.
        self.select_normal(b)
        set_bundle_role(self.group, a.id, "SIMPLIFIED")
        a_first.represented_by = b_first
        a_first.save(update_fields=["represented_by"])

        self.assertEqual(version_bundles(self.group), {"NORMAL": [a_first]})
        with patch("course.version_assignment.classify_group_versions") as classify:
            classify.return_value = {
                b_first.id: {"slot": "ELABORATED", "confidence": 0.9, "reason": "More detail."},
            }
            state = assign_group_versions(self.group, use_llm=True)

        self.assertEqual(state["normal_material_id"], a.id)
        self.assertEqual(state["bundle_roles"], {b.id: "ELABORATED"})
        self.group.refresh_from_db()
        a_first.refresh_from_db()
        self.assertEqual(self.group.version_selection["normal_material_id"], a.id)
        self.assertIsNone(a_first.represented_by_id)
        self.assertEqual(classify.call_args.kwargs["representative"].id, a_first.id)
        self.assertEqual([item.id for item in classify.call_args.args[0]], [b_first.id])

    def test_teacher_can_explicitly_replace_the_first_pdf(self):
        a, a_first = self.add_source("PDF A")
        b, b_first = self.add_source("PDF B")
        self.select_normal(a)

        assign_source_as_representative(self.group, b_first)

        self.group.refresh_from_db()
        self.assertEqual(self.group.version_selection["normal_assigned_by"], "teacher")
        self.assertEqual(version_bundles(self.group)["NORMAL"], [b_first])
        self.assertTrue(LearningObject.objects.filter(pk=a_first.pk).exists())

    def test_confirming_first_pdf_clears_legacy_llm_baseline(self):
        a, a_first = self.add_source("PDF A")
        b, _b_first = self.add_source("PDF B")
        self.select_normal(b)
        set_bundle_role(self.group, a.id, "SIMPLIFIED")

        assign_source_as_representative(self.group, a_first)

        self.group.refresh_from_db()
        self.assertEqual(self.group.version_selection["normal_material_id"], a.id)
        self.assertEqual(self.group.version_selection["normal_assigned_by"], "teacher")
        self.assertEqual(self.group.version_selection["bundle_roles"], {})

    def test_teacher_can_confirm_replacement_through_version_review_api(self):
        a, a_first = self.add_source("PDF A")
        _b, b_first = self.add_source("PDF B")
        self.select_normal(a)
        a_first.delete()

        response = authenticated_api_client().post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}/version-assignment/",
            {"learning_object_id": b_first.id, "slot": "NORMAL"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.group.refresh_from_db()
        self.assertEqual(version_bundles(self.group), {"NORMAL": [b_first]})

    def test_moving_lead_keeps_same_pdf_normal_when_another_object_remains(self):
        a, a_first = self.add_source("PDF A")
        a_next = LearningObject.objects.create(
            material=a, group=self.group, title="More solids", order=1,
            content="More facts about solids.",
        )
        b, b_first = self.add_source("PDF B")
        assign_group_versions(self.group)
        set_bundle_role(self.group, b.id, "SIMPLIFIED")
        b_first.represented_by = a_first
        b_first.save(update_fields=["represented_by"])

        release_from_group(a_first, [a_next, b_first])
        LearningObject.objects.filter(pk=a_first.pk).update(group=None)

        b_first.refresh_from_db()
        self.group.refresh_from_db()
        self.assertEqual(b_first.represented_by_id, a_next.id)
        self.assertFalse(assign_group_versions(self.group)["normal_replacement_needed"])
        self.assertEqual(version_bundles(self.group), {"NORMAL": [a_next], "SIMPLIFIED": [b_first]})


class ExistingCourseRebaseMigrationTests(NormalSourceFixture, TestCase):
    """The migration archives old choices and unpublishes changed topics."""

    def _run_migration(self):
        migration = import_module("course.migrations.0010_first_relevant_pdf_is_normal")
        migration.rebase_existing_concepts(apps, SimpleNamespace(connection=connection))

    def test_old_llm_baseline_is_archived_without_deleting_pdf_or_generated_text(self):
        a, a_first = self.add_source("PDF A")
        b, b_first = self.add_source("PDF B")
        self.select_normal(b)
        set_bundle_role(self.group, a.id, "SIMPLIFIED")
        a_first.represented_by = b_first
        a_first.save(update_fields=["represented_by"])
        self.add_generated_versions(b_first)
        self.topic.published = True
        self.topic.save(update_fields=["published"])

        self._run_migration()

        self.group.refresh_from_db()
        a_first.refresh_from_db()
        self.topic.refresh_from_db()
        selection = self.group.version_selection
        self.assertEqual(selection["normal_material_id"], a.id)
        self.assertEqual(selection["baseline_rebase_backup"]["normal_material_id"], b.id)
        self.assertEqual(selection["bundle_roles"], {})
        self.assertIsNone(a_first.represented_by_id)
        self.assertFalse(self.topic.published)
        self.assertEqual(LessonVariant.objects.filter(learning_object=b_first).count(), 2)

    def test_migration_keeps_an_explicit_teacher_primary(self):
        a, _a_first = self.add_source("PDF A")
        b, b_first = self.add_source("PDF B")
        self.select_normal(a)
        assign_source_as_representative(self.group, b_first)

        self._run_migration()

        self.group.refresh_from_db()
        self.assertEqual(self.group.version_selection["normal_material_id"], b.id)
        self.assertEqual(self.group.version_selection["normal_assigned_by"], "teacher")
