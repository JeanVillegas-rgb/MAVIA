"""The review payload must describe a concept by role, not by "lead + leftovers".

The final-review screen used to render a concept as its representative object
plus every other member labelled "Other variation". That describes the old
model, where a concept held one object per PDF. Under bundles it is wrong in
three ways, all visible on the real topic 152:

* Normal was the representative's own text, so three of the four objects of
  "Comparing the Three States" were shown as variations of themselves.
* A generated version showed only the representative's segment -- one of the
  four that had actually been written.
* A version a PDF supplies was printed in full AND again object by object, so
  every word appeared twice with nothing saying they were the same thing.

The payload now carries one entry per role, each naming where it came from and
which objects it is made of, so the screen can show every object exactly once.
"""

from django.test import TestCase
from django.utils import timezone

from course.models import LessonVariant
from course.version_assignment import set_bundle_role
from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)
from user.models import User


class ConceptRolePayloadTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 1 Science")
        self.topic = OutlineNode.objects.create(
            course=self.course, title="Solid, Liquid and Gas", order=0, depth=0,
        )
        confirmed = {"learning_objects_confirmed": True}
        self.first = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="PDF A",
            generated_json=dict(confirmed),
        )
        self.second = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="PDF B",
            generated_json=dict(confirmed),
        )
        self.group = LearningObjectGroup.objects.create(
            outline_node=self.topic, label="Comparing the Three States",
        )
        self.normal = [
            self._object(self.first, "Shape", "Solids keep their shape.", 0),
            self._object(self.first, "Volume", "Gases expand to fill space.", 1),
            self._object(self.first, "Particle arrangement", "Gas particles are widely spaced.", 2),
            self._object(self.first, "Flow", "Liquids and gases can flow.", 3),
        ]
        self.supplied = [
            self._object(self.second, "Comparing the Three States", "The table summarizes the differences.", 0),
            self._object(self.second, "5. Comparing the Three States", "Solids hold shape; gases fill the space.", 1),
        ]
        for item in self.supplied:
            item.represented_by = self.normal[0]
            item.save(update_fields=["represented_by"])
        set_bundle_role(self.group, self.second.id, "SIMPLIFIED")

        self.user = User.objects.create(username="t", email="t@example.com", role="TEACHER")

    def _object(self, material, title, content, order):
        return LearningObject.objects.create(
            material=material, group=self.group, title=title, content=content,
            order=order, section_title="Comparing the Three States",
        )

    def _slots(self):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.user)
        response = client.get(
            f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}/learning-resources/"
        )
        self.assertEqual(response.status_code, 200)
        group = [g for g in response.data["learning_object_groups"] if g["id"] == self.group.id][0]
        return group["versions"]["slots"]

    def test_normal_is_a_slot_of_its_own_carrying_the_whole_bundle(self):
        normal = self._slots().get("normal")

        self.assertIsNotNone(normal, "the screen had no Normal to show but the lead's own text")
        for item in self.normal:
            self.assertIn(item.content, normal["text"])

    def test_each_slot_names_the_objects_it_is_made_of(self):
        normal = self._slots()["normal"]

        self.assertEqual(
            [entry["id"] for entry in normal["objects"]],
            [item.id for item in self.normal],
        )
        self.assertEqual(normal["objects"][1]["title"], "Volume")

    def test_a_slot_says_whether_a_pdf_supplied_it_or_it_was_generated(self):
        slots = self._slots()

        self.assertEqual(slots["normal"]["source"], "pdf")
        self.assertEqual(slots["normal"]["material"], self.first.id)
        self.assertEqual(slots["simplified"]["source"], "pdf")
        self.assertEqual(slots["simplified"]["material"], self.second.id)

    def test_a_supplied_slot_carries_its_own_objects_not_the_normal_ones(self):
        simplified = self._slots()["simplified"]

        self.assertEqual(
            [entry["id"] for entry in simplified["objects"]],
            [item.id for item in self.supplied],
        )

    def test_a_generated_slot_carries_every_segment_not_only_the_leads(self):
        for item in self.normal:
            LessonVariant.objects.create(
                learning_object=item, variant="ELABORATED",
                narration=f"Elaborated {item.title}.",
                origin=LessonVariant.Origin.GENERATED,
            )

        elaborated = self._slots()["elaborated"]

        self.assertEqual(elaborated["source"], "generated")
        self.assertEqual(len(elaborated["objects"]), len(self.normal))
        for item in self.normal:
            self.assertIn(f"Elaborated {item.title}.", elaborated["text"])

    def test_a_generated_version_short_of_its_bundle_is_not_offered(self):
        """Three quarters of a version must not read as a whole one."""
        LessonVariant.objects.create(
            learning_object=self.normal[0], variant="ELABORATED",
            narration="Only the lead was written.",
            origin=LessonVariant.Origin.GENERATED,
        )

        self.assertIsNone(self._slots().get("elaborated"))

    def test_a_supplied_role_still_outranks_a_generated_row(self):
        for item in self.normal:
            LessonVariant.objects.create(
                learning_object=item, variant="SIMPLIFIED",
                narration=f"Generated {item.title}.",
                origin=LessonVariant.Origin.GENERATED,
            )

        simplified = self._slots()["simplified"]

        self.assertEqual(simplified["source"], "pdf")
        self.assertNotIn("Generated Shape.", simplified["text"])

    def test_a_generated_segment_shows_what_was_written_not_its_source(self):
        """An Elaborated block must not print the Normal text back at you."""
        for item in self.normal:
            LessonVariant.objects.create(
                learning_object=item, variant="ELABORATED",
                narration=f"Elaborated {item.title}.",
                origin=LessonVariant.Origin.GENERATED,
            )

        objects = self._slots()["elaborated"]["objects"]

        self.assertEqual(
            [entry["text"] for entry in objects],
            [f"Elaborated {item.title}." for item in self.normal],
        )
        # The object is still named, so the teacher can see which segment is which.
        self.assertEqual([entry["title"] for entry in objects],
                         [item.title for item in self.normal])
