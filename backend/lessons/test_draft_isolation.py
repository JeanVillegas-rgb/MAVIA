"""Editing a PDF that is not confirmed never changes the approved PDFs' concepts.

Deleting one object from a brand-new upload used to re-run the whole grouping
refresh -- section joins released and redone across the topic, similarity
matching, the review queue rebuilt -- for every other PDF holding printed
questions, approved lesson PDFs included. A draft edit now reaches no other
PDF at all, their question pairs included: those are redone on reconfirm.
"""

from unittest.mock import patch

from django.test import TestCase

from .models import CourseGroup, LearningMaterial, LearningObject, LearningObjectGroup, OutlineNode, Question
from .services import learning_resource_linker as linker
from .services.learning_resource_linker import SECTION_JOINS_KEY, attach_orphan_objects_to_their_section
from .tests import authenticated_api_client


class DraftEditIsolationTests(TestCase):
    def setUp(self):
        self.client = authenticated_api_client()
        self.course = CourseGroup.objects.create(title="Science")
        self.node = OutlineNode.objects.create(course=self.course, title="States of matter")
        self.approved = self.material("Approved", confirmed=True)
        self.matter = self.add(self.approved, "Matter", "Matter", 0)
        self.solid = self.add(self.approved, "Solid", "Matter", 1)
        # Nothing else teaches "Solid", so it is joined to its section.
        attach_orphan_objects_to_their_section(self.approved)
        self.approved.refresh_from_db()
        # Printed questions make the approved PDF one the edit loop visits.
        Question.objects.create(material=self.approved, prompt="What is a solid?", order=0)
        self.draft = self.material("Brand-new upload", confirmed=False)
        self.gas = self.add(self.draft, "Gas", "Gas", 0)
        self.add(self.draft, "Plasma", "Plasma", 1)

    def material(self, title, *, confirmed):
        return LearningMaterial.objects.create(
            course=self.course, outline_node=self.node, title=title,
            status=LearningMaterial.Status.COMPLETED,
            generated_json={"learning_objects_confirmed": confirmed},
        )

    def add(self, material, title, section, order):
        return LearningObject.objects.create(
            material=material, title=title, section_title=section, content=f"{title} text.",
            order=order,
            group=LearningObjectGroup.objects.create(outline_node=self.node, label=title),
        )

    def delete_from_draft(self):
        return self.client.delete(
            f"/api/courses/{self.course.id}/materials/{self.draft.id}/learning-objects/{self.gas.id}/"
        )

    def test_the_approved_pdf_is_not_regrouped(self):
        regrouped = []
        real = linker.refresh_learning_object_match_suggestions

        def spy(material):
            regrouped.append(material.id)
            return real(material)

        with patch.object(linker, "refresh_learning_object_match_suggestions", side_effect=spy):
            response = self.delete_from_draft()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.approved.id, regrouped)

    def test_the_approved_section_joins_are_left_as_they_were(self):
        joins_before = dict(self.approved.generated_json[SECTION_JOINS_KEY])
        groups_before = set(LearningObjectGroup.objects.values_list("id", flat=True))

        with patch.object(linker, "release_section_joins", wraps=linker.release_section_joins) as release:
            self.delete_from_draft()

        # Released and redone, the joins usually came back the same -- but
        # only usually, and only after rewriting approved data on every edit.
        release.assert_not_called()

        self.approved.refresh_from_db()
        self.solid.refresh_from_db()
        self.assertEqual(self.approved.generated_json.get(SECTION_JOINS_KEY), joins_before)
        self.assertEqual(self.solid.group_id, self.matter.group_id)
        # Nothing was released and re-created along the way: every group left
        # is one that existed before (empty ones may be cleaned up).
        groups_after = set(LearningObjectGroup.objects.values_list("id", flat=True))
        self.assertLessEqual(groups_after, groups_before)

    def test_the_approved_pdfs_question_pairs_are_left_alone(self):
        repaired = []
        real = linker.refresh_question_learning_object_links

        def spy(material):
            repaired.append(material.id)
            return real(material)

        with patch.object(linker, "refresh_question_learning_object_links", side_effect=spy),                 patch("lessons.views.refresh_question_learning_object_links", side_effect=spy, create=True):
            self.delete_from_draft()

        self.assertNotIn(self.approved.id, repaired)
