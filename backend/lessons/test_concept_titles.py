"""Concepts that share a name are told apart by number, in reading order.

Measured on the real upload: one heading split into its definition, its
properties and its examples gave three concepts all titled "Solid". Grouping
was right to keep them apart; only the names were useless. The shown title is
made distinct -- the stored label, which every edit reads, is not touched.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from lessons.models import (
    CourseGroup,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    OutlineNode,
)
from lessons.services.concept_titles import display_titles
from user.models import User


class ConceptTitleTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.topic = OutlineNode.objects.create(course=self.course, title="Matter", order=0, depth=0)
        confirmed = {"learning_objects_confirmed": True}
        self.first = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="PDF A", generated_json=dict(confirmed),
        )
        self.second = LearningMaterial.objects.create(
            course=self.course, outline_node=self.topic, title="PDF B", generated_json=dict(confirmed),
        )

    def _concept(self, label, content, material=None, kind="text", **selection):
        group = LearningObjectGroup.objects.create(
            outline_node=self.topic, label=label, version_selection=selection,
        )
        item = LearningObject.objects.create(
            material=material or self.first, group=group, title=label,
            content=content, order=0, kind=kind,
        )
        return group, [item]

    def _titles(self, *concepts):
        return display_titles([
            (group, members, position) for position, (group, members) in enumerate(concepts)
        ])

    def test_concepts_sharing_a_name_are_numbered_in_reading_order(self):
        first = self._concept("Solid", "A solid is a state of matter.")
        second = self._concept("Solid", "Properties of Solids: fixed shape.")
        third = self._concept("Solid", "Example: A book keeps its shape.")

        titles = self._titles(first, second, third)

        self.assertEqual(
            [titles[first[0].id], titles[second[0].id], titles[third[0].id]],
            ["Solid I", "Solid II", "Solid III"],
        )

    def test_names_differing_only_in_case_count_as_shared(self):
        lower = self._concept("Liquid", "A liquid flows.")
        upper = self._concept("LIQUID", "A liquid takes its container's shape.", material=self.second)

        titles = self._titles(lower, upper)

        self.assertEqual([titles[lower[0].id], titles[upper[0].id]], ["Liquid I", "LIQUID II"])

    def test_a_unique_name_is_left_alone(self):
        concept = self._concept("Melting", "Melting happens when a solid is heated.")

        self.assertEqual(self._titles(concept)[concept[0].id], "Melting")

    def test_a_teachers_label_is_shown_exactly_as_typed(self):
        locked = self._concept("Solid", "A solid is a state of matter.", label_locked=True)
        other = self._concept("Solid", "Example: A book keeps its shape.")

        titles = self._titles(locked, other)

        self.assertEqual(titles[locked[0].id], "Solid")
        self.assertEqual(titles[other[0].id], "Solid")

    def test_a_figure_named_only_by_its_number_is_titled_by_its_caption(self):
        concept = self._concept(
            "Figure 1", "Figure 1. The main parts of a flower involved in reproduction.", kind="image",
        )

        self.assertEqual(
            self._titles(concept)[concept[0].id],
            "The main parts of a flower involved in reproduction",
        )

    def test_a_narrated_figure_is_titled_by_its_recorded_caption(self):
        """Measured live: narrated figures were labelled by the narration's
        first sentence, "The image shows a flower with several distinct parts"."""
        narration = "The image shows a flower with several distinct parts arranged around a core"
        url = "/media/extracted_images/flower.png"
        self.first.generated_json["image_descriptions"] = [{
            "image_url": url, "caption": "Figure 1. The main parts of a flower involved in reproduction.",
        }]
        self.first.save(update_fields=["generated_json"])
        group = LearningObjectGroup.objects.create(outline_node=self.topic, label=narration)
        figure = LearningObject.objects.create(
            material=self.first, group=group, title=narration, content=narration,
            kind="image", image_url=url,
        )

        self.assertEqual(
            display_titles([(group, [figure], 0)])[group.id],
            "The main parts of a flower involved in reproduction",
        )

    def test_a_named_figure_keeps_its_name(self):
        concept = self._concept(
            "Parts of a Flower", "Figure 1. The main parts of a flower.", kind="image",
        )

        self.assertEqual(self._titles(concept)[concept[0].id], "Parts of a Flower")

    def test_a_figure_without_a_caption_keeps_its_number(self):
        concept = self._concept("Figure 3", "A drawing of a seed.", kind="image")

        self.assertEqual(self._titles(concept)[concept[0].id], "Figure 3")

    def test_a_concept_named_by_a_sentence_takes_its_members_short_heading(self):
        """Measured live: one PDF's passage had no heading and was titled by its
        first sentence; the other PDF headed the same passage "Matter"."""
        sentence = "Matter is anything that has mass and takes up space"
        group, members = self._concept(sentence, "Matter is anything that has mass.")
        members.append(LearningObject.objects.create(
            material=self.second, group=group, title="Matter",
            content="Matter is anything that has mass and occupies space.", order=0,
        ))

        self.assertEqual(self._titles((group, members))[group.id], "Matter")

    def test_a_sentence_name_with_no_short_heading_is_kept(self):
        sentence = "Matter is anything that has mass and takes up space"
        concept = self._concept(sentence, "Matter is anything that has mass.")

        self.assertEqual(self._titles(concept)[concept[0].id], sentence)

    def test_a_teachers_sentence_name_is_kept(self):
        sentence = "Everything around us that has mass and volume"
        group, members = self._concept(sentence, "Matter has mass.", label_locked=True)
        members.append(LearningObject.objects.create(
            material=self.second, group=group, title="Matter", content="Matter has mass.", order=0,
        ))

        self.assertEqual(self._titles((group, members))[group.id], sentence)

    def test_the_api_sends_the_title_beside_the_stored_label(self):
        first = self._concept("Solid", "A solid is a state of matter.")
        second = self._concept("Solid", "Example: A book keeps its shape.")
        client = APIClient()
        client.force_authenticate(User.objects.create(username="t", email="t@example.com", role="TEACHER"))

        response = client.get(
            f"/api/courses/{self.course.id}/outline-nodes/{self.topic.id}/learning-resources/"
        )

        self.assertEqual(response.status_code, 200)
        by_id = {group["id"]: group for group in response.data["learning_object_groups"]}
        self.assertEqual(by_id[first[0].id]["display_title"], "Solid I")
        self.assertEqual(by_id[second[0].id]["display_title"], "Solid II")
        # The stored name every edit reads is untouched.
        self.assertEqual(by_id[first[0].id]["label"], "Solid")
