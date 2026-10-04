"""Undo and move on the learning path review screen.

Every change a teacher confirms returns the prior state of each pair it
touched; restore puts exactly that back. See
docs/superpowers/specs/2026-09-29-learning-path-graph-screen-design.md, 5.3-5.4.
"""

from lessons.models import LearningObject, LearningObjectGroup, OutlineNode

from .models import ConceptPrerequisite
from .tests import TopicFixture


class LinkEditingFixture(TopicFixture):
    def setUp(self):
        super().setUp()
        self.teacher = self._client("TEACHER")
        self.base = f"/api/learning-path/topics/{self.topic.id}"

    def _post(self, path, body):
        return self.teacher.post(f"{self.base}/links/{path}", body, format="json")

    def _ids(self, before, after):
        return {"prerequisite_concept_id": self.groups[before].id, "dependent_concept_id": self.groups[after].id}

    def _row(self, before, after):
        return ConceptPrerequisite.objects.filter(
            prerequisite=self.groups[before], dependent=self.groups[after],
        ).first()

    def _state(self, before, after):
        row = self._row(before, after)
        return None if row is None else (row.status, row.source)


class UndoTests(LinkEditingFixture):
    def test_adding_returns_an_undo_that_deletes_the_new_link(self):
        response = self._post("", self._ids("Liquid", "Solid"))
        undo = response.json()["undo"]

        self.assertEqual(undo, [{
            "prerequisite_id": self.groups["Liquid"].id, "dependent_id": self.groups["Solid"].id, "prior": None,
        }])
        restored = self._post("restore/", {"undo": undo})
        self.assertEqual(restored.status_code, 200, restored.json())
        self.assertIsNone(self._row("Liquid", "Solid"))

    def test_undoing_an_approval_puts_the_suggestion_back(self):
        link = self._link("Liquid", "Solid", "pending")
        undo = self._post(f"{link.id}/decision/", {"status": "approved"}).json()["undo"]

        self._post("restore/", {"undo": undo})

        link.refresh_from_db()
        self.assertEqual((link.status, link.source, link.decided_at), ("pending", "derived", None))

    def test_undoing_a_removal_brings_the_link_back(self):
        link = self._link("Matter", "Solid", "accepted")
        undo = self._post(f"{link.id}/decision/", {"status": "rejected"}).json()["undo"]

        self._post("restore/", {"undo": undo})

        self.assertEqual(self._state("Matter", "Solid"), ("accepted", "derived"))

    def test_undo_recreates_a_link_deleted_in_between(self):
        link = self._link("Matter", "Solid", "accepted")
        undo = self._post(f"{link.id}/decision/", {"status": "rejected"}).json()["undo"]
        ConceptPrerequisite.objects.all().delete()

        response = self._post("restore/", {"undo": undo})

        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(self._state("Matter", "Solid"), ("accepted", "derived"))

    def test_restore_refuses_a_loop_and_changes_nothing(self):
        self._link("Matter", "Liquid", "approved")
        records = [{
            "prerequisite_id": self.groups["Liquid"].id, "dependent_id": self.groups["Matter"].id,
            "prior": {"status": "approved", "source": "teacher", "decided_at": None},
        }]

        response = self._post("restore/", {"undo": records})

        self.assertEqual(response.status_code, 400)
        self.assertIn("loop", response.json()["detail"])
        self.assertIsNone(self._row("Liquid", "Matter"))

    def test_restore_refuses_a_concept_from_another_topic(self):
        other_topic = OutlineNode.objects.create(course=self.course, title="Other", order=1, depth=0)
        stranger = LearningObjectGroup.objects.create(outline_node=other_topic, label="Stranger")
        records = [{"prerequisite_id": stranger.id, "dependent_id": self.groups["Solid"].id, "prior": None}]

        response = self._post("restore/", {"undo": records})

        self.assertEqual(response.status_code, 400)
        self.assertIn("not part of this topic", response.json()["detail"])

    def test_restore_without_records_is_refused(self):
        response = self._post("restore/", {})

        self.assertEqual(response.status_code, 400)

    def test_students_cannot_restore(self):
        response = self._client("STUDENT").post(f"{self.base}/links/restore/", {"undo": []}, format="json")

        self.assertEqual(response.status_code, 403)

    def test_deciding_a_link_that_no_longer_exists_is_refused(self):
        response = self._post("999999/decision/", {"status": "approved"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("no longer exists", response.json()["detail"])


class MoveTests(LinkEditingFixture):
    def setUp(self):
        super().setUp()
        group = LearningObjectGroup.objects.create(outline_node=self.topic, label="Gas")
        self.objects["Gas"] = LearningObject.objects.create(
            material=self.material, group=group, title="Gas", content="Gas is taught here.", order=3,
        )
        self.groups["Gas"] = group

    def test_moving_replaces_every_current_prerequisite(self):
        self._link("Matter", "Gas", "accepted")
        self._link("Solid", "Gas", "approved")

        response = self._post("move/", self._ids("Liquid", "Gas"))

        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(self._state("Matter", "Gas"), ("rejected", "teacher"))
        self.assertEqual(self._state("Solid", "Gas"), ("rejected", "teacher"))
        self.assertEqual(self._state("Liquid", "Gas"), ("approved", "teacher"))

    def test_undoing_a_move_restores_both_halves(self):
        self._link("Matter", "Gas", "accepted")
        self._link("Solid", "Gas", "approved")
        undo = self._post("move/", self._ids("Liquid", "Gas")).json()["undo"]

        self._post("restore/", {"undo": undo})

        self.assertEqual(self._state("Matter", "Gas"), ("accepted", "derived"))
        self.assertEqual(self._state("Solid", "Gas"), ("approved", "teacher"))
        self.assertIsNone(self._row("Liquid", "Gas"))

    def test_a_move_that_would_loop_changes_nothing(self):
        self._link("Matter", "Gas", "accepted")
        self._link("Gas", "Liquid", "approved")

        response = self._post("move/", self._ids("Liquid", "Gas"))

        self.assertEqual(response.status_code, 400)
        self.assertIn("loop", response.json()["detail"])
        self.assertEqual(self._state("Matter", "Gas"), ("accepted", "derived"))
        self.assertIsNone(self._row("Liquid", "Gas"))

    def test_moving_under_the_current_prerequisite_rejects_nothing(self):
        self._link("Matter", "Gas", "accepted")

        self._post("move/", self._ids("Matter", "Gas"))

        self.assertEqual(self._state("Matter", "Gas"), ("approved", "teacher"))
        self.assertFalse(ConceptPrerequisite.objects.filter(status="rejected").exists())

    def test_a_concept_cannot_move_under_itself(self):
        response = self._post("move/", self._ids("Gas", "Gas"))

        self.assertEqual(response.status_code, 400)
