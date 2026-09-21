"""What a placement has to tidy up behind it.

``place_unit`` is reached from three directions -- automatic placement, the
teacher accepting a card, and the Move out / Move to buttons -- and every one
of them leaves a concept behind. The bookkeeping that concept carries about the
object that is going (who it represented, what role its PDF's bundle had) is
undone here, once, so all three behave the same.
"""

import os
from unittest.mock import patch

from django.test import TestCase
from course.version_assignment import (
    bundle_role_provenance,
    bundle_roles,
    set_bundle_role,
)
from .models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    LearningObjectMatchSuggestion,
    OutlineNode,
)
from .services.unit_matching import place_unit, refresh_heading_unit_suggestions
from .test_semantic_grouping import FakeRuntime
from .test_unit_matching import SEMANTIC_ENV
from .tests import authenticated_api_client


class StrandedCompanionTests(TestCase):
    """A unit leaves a concept whose Normal it was; a counterpart stays."""

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.a = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A",
            generated_json=dict(confirmed),
        )
        self.b = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="B",
            generated_json=dict(confirmed),
        )
        # The concept the unit is placed into.
        self.target = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solid")
        self.solid_a = self._object(self.a, self.target, "Solid", "", 0)
        # The concept the unit leaves: B's "Solids" run leads it, and A's
        # "Liquid" is taught through that lead.
        self.left_behind = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solids")
        self.solids_b = self._object(self.b, self.left_behind, "Solids", "Solids", 0)
        self.diagram_b = self._object(
            self.b,
            LearningObjectGroup.objects.create(outline_node=self.topic, label="Diagram"),
            "Diagram description", "Solids", 1,
        )
        self.liquid_a = self._object(self.a, self.left_behind, "Liquid", "", 1)
        self.left_behind.version_selection = {"normal_material_id": self.b.id}
        self.left_behind.save(update_fields=["version_selection"])
        set_bundle_role(self.left_behind, self.a.id, "SIMPLIFIED")
        LearningObject.objects.filter(pk=self.liquid_a.pk).update(represented_by=self.solids_b)

    def _object(self, material, group, title, section, order, kind="text"):
        return LearningObject.objects.create(
            material=material, group=group, title=title,
            content=f"{title} text for this concept.", section_title=section,
            order=order, kind=kind,
        )

    def _refresh(self, score):
        runtime = FakeRuntime()
        runtime.pair_scores = lambda pairs: [score for _ in pairs]
        with patch.dict(os.environ, SEMANTIC_ENV):
            return refresh_heading_unit_suggestions(self.topic, runtime_instance=runtime)

    def _assert_not_stranded(self):
        self.liquid_a.refresh_from_db()
        self.solids_b.refresh_from_db()
        self.assertEqual(self.liquid_a.group_id, self.left_behind.id)
        self.assertNotEqual(self.solids_b.group_id, self.left_behind.id)
        # Every consumer of a published lesson filters on this being null, so
        # a companion still pointing at the object that left disappears from
        # the lesson, its audio and the mobile package.
        self.assertIsNone(self.liquid_a.represented_by_id)

    def test_automatic_placement_releases_the_companion_it_leaves(self):
        counts = self._refresh(0.8)

        self.assertGreaterEqual(counts["placed"], 1)
        self._assert_not_stranded()

    def test_accepting_a_card_releases_the_companion_it_leaves(self):
        self._refresh(0.45)
        suggestion = LearningObjectMatchSuggestion.objects.get(
            status=LearningObjectMatchSuggestion.Status.PENDING,
        )
        client = authenticated_api_client()

        response = client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}"
            f"/match-suggestions/{suggestion.id}/accept/",
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self._assert_not_stranded()

    def test_the_departing_bundles_role_is_forgotten_by_the_concept(self):
        # A's "Liquid" leads this concept and B's run is its Simplified; when
        # the run leaves, the role must go with it, or a different object of
        # B's arriving later inherits a decision about text that has gone.
        self.left_behind.version_selection = {"normal_material_id": self.a.id}
        self.left_behind.save(update_fields=["version_selection"])
        LearningObject.objects.filter(pk=self.liquid_a.pk).update(represented_by=None)
        set_bundle_role(self.left_behind, self.b.id, "ELABORATED")
        self.assertEqual(bundle_roles(self.left_behind), {self.b.id: "ELABORATED"})

        place_unit([self.solids_b, self.diagram_b], self.target)

        self.left_behind.refresh_from_db()
        selection = self.left_behind.version_selection
        for store in ("bundle_roles", "bundle_roles_assigned_by", "bundle_roles_decided_at"):
            self.assertNotIn(str(self.b.id), selection.get(store) or {}, store)

    def test_a_material_returning_with_other_objects_inherits_no_role(self):
        set_bundle_role(self.target, self.b.id, "ELABORATED", assigned_by="teacher")
        self.assertEqual(
            (self.target.version_selection.get("bundle_roles") or {}).get(str(self.b.id)),
            "ELABORATED",
        )

        place_unit([self.solids_b, self.diagram_b], self.target)

        self.target.refresh_from_db()
        selection = self.target.version_selection
        for store in ("bundle_roles", "bundle_roles_assigned_by", "bundle_roles_decided_at"):
            self.assertNotIn(str(self.b.id), selection.get(store) or {}, store)


class AutomaticConceptNameTests(TestCase):
    """A bundle often opens with a figure whose own title names nothing."""

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.a = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A",
            generated_json=dict(confirmed),
        )
        self.b = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="B",
            generated_json=dict(confirmed),
        )
        # A's run: an unnamed figure first, then the section it illustrates.
        self.figure = self._object(self.a, "", "Comparing the Three States", 0, kind="image")
        self.shape = self._object(self.a, "Shape", "Comparing the Three States", 1)
        # B names the same thing in one object.
        self.compare_b = self._object(self.b, "Comparing the Three States", "", 0)

    def _object(self, material, title, section, order, kind="text"):
        return LearningObject.objects.create(
            material=material, group=None, title=title,
            content=f"{title or 'figure'} text for this concept.",
            section_title=section, order=order, kind=kind,
        )

    def test_a_new_concept_is_named_after_the_bundles_heading(self):
        runtime = FakeRuntime()
        runtime.pair_scores = lambda pairs: [0.8 for _ in pairs]
        with patch.dict(os.environ, SEMANTIC_ENV):
            refresh_heading_unit_suggestions(self.topic, runtime_instance=runtime)

        self.figure.refresh_from_db()
        self.assertIsNotNone(self.figure.group_id)
        self.assertEqual(self.figure.group.label, "Comparing the Three States")


class PlainConnectAcceptTests(TestCase):
    """Accepting a one-object-per-side card is a placement too.

    It takes a different branch from `place_unit`, and that branch has to leave
    the same two things right: the concept's name, and the roles stored against
    a PDF that is only now contributing to it.
    """

    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="States")
        confirmed = {"learning_objects_confirmed": True}
        self.a = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="A",
            generated_json=dict(confirmed),
        )
        self.b = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="B",
            generated_json=dict(confirmed),
        )
        self.client = authenticated_api_client()

    def _object(self, material, title, section="", order=0, group=None):
        return LearningObject.objects.create(
            material=material, group=group, title=title, section_title=section,
            order=order, content=f"{title or 'figure'} text for this concept.",
        )

    def _accept(self, source, candidate):
        suggestion = LearningObjectMatchSuggestion.objects.create(
            outline_node=self.topic,
            source_learning_object=source,
            candidate_learning_object=candidate,
            similarity_score=0.5,
            confidence=LearningObjectMatchSuggestion.Confidence.MEDIUM,
        )
        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}"
            f"/match-suggestions/{suggestion.id}/accept/",
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)

    def test_a_new_concept_from_a_lone_object_is_named_after_that_object(self):
        """Design 3.5: the heading names a concept only for a bundle of 2+.

        Solid, Liquid and Gas all sit under the heading "Matter" in the first
        PDF, so naming each of them after that heading gave three concepts one
        name and the criteria's same-name veto then deleted their edges.
        """
        solid_a = self._object(self.a, "Solid", "Matter", 0)
        solid_b = self._object(self.b, "Solid", "", 0)

        self._accept(solid_a, solid_b)

        solid_b.refresh_from_db()
        self.assertEqual(solid_b.group.label, "Solid")

    def test_a_material_joining_the_concept_inherits_no_stale_role(self):
        group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Solid")
        solid_a = self._object(self.a, "Solid", "", 0, group=group)
        # B once contributed here and left; the ruling about its old text must
        # not be handed to the object arriving now.
        set_bundle_role(group, self.b.id, "ELABORATED", assigned_by="teacher")
        solids_b = self._object(self.b, "Solids", "", 0)

        self._accept(solid_a, solids_b)

        group.refresh_from_db()
        # The role may be decided again from the arriving text -- what must not
        # survive is the teacher's ruling about text that is gone.
        self.assertNotEqual(bundle_roles(group).get(self.b.id), "ELABORATED")
        self.assertNotEqual(bundle_role_provenance(group).get(self.b.id), "teacher")
