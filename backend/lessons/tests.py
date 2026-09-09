from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from unittest.mock import patch


def authenticated_api_client():
    """The course-authoring API is teacher/admin only. Every test that hits it
    needs a signed-in teacher; this returns one force-authenticated client."""
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username="lessons_test_teacher",
        defaults={
            "email": "lessons_test_teacher@example.com",
            "role": User.Role.TEACHER,
            "is_verified": True,
        },
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client

from .models import (
    CourseGroup,
    CourseOutline,
    LearningMaterial,
    LearningObject,
    LearningObjectGroup,
    LearningObjectMatchSuggestion,
    OutlineNode,
    Question,
    QuestionLearningObjectLink,
)
from .serializers import CourseDetailSerializer, LearningMaterialSerializer
from .services.content_generator import (
    _text_blocks_from_transcription,
    build_learning_objects_from_pdf_blocks,
    build_narration_script_from_learning_objects,
    build_section_learning_objects,
    balance_learning_object_chunks,
    _borderless_table_regions,
    _instructional_table_rows,
    _plausible_table_bbox,
    describe_pdf_images,
    exclude_text_blocks_inside_tables,
    extract_meaningful_pdf_images,
    choose_outline_node_for_material,
    review_learning_objects_for_bvi_learners,
    validate_outline_node_for_material,
)
from .services.audio_generator import generate_material_audio_playlist
from .services.instructional_content_classifier import (
    classify_instructional_blocks,
    clean_block_text,
    detect_instructional_document_role,
    extract_pdf_text_blocks,
)
from .services.learning_resource_linker import (
    detected_question_payloads,
    ensure_learning_object_groups,
    learning_object_match_evidence,
    question_pairing_debug_configuration,
    refresh_learning_object_match_suggestions,
    refresh_question_learning_object_links,
    synchronize_detected_questions,
)
from .features.pdf_processing.use_cases import (
    PdfProcessingUseCaseError,
    upload_course_outline,
    upload_course_pdf,
    upload_learning_material,
)
from .services.outline_parser import (
    ParsedOutlineNode,
    _build_outline_candidates,
    _clean_related_info,
    _extract_pdf_table_outline_text,
    _looks_like_plain_outline_title_start,
    _merge_persisted_nodes,
    extract_outline_text,
    parse_outline_text,
    is_course_outline_document,
)


class CumulativeCourseOutlineTests(TestCase):
    @patch("lessons.features.pdf_processing.use_cases.upload_course_outline")
    @patch("lessons.features.pdf_processing.use_cases.is_course_outline_pdf", return_value=True)
    def test_unified_upload_routes_detected_outline_to_hierarchy_merge(
        self,
        _is_outline,
        upload_outline,
    ):
        course = CourseGroup.objects.create(title="Science")
        uploaded = SimpleUploadedFile("outline.pdf", b"%PDF-1.4 outline")
        upload_outline.return_value = (course, False)

        upload_type, material, reused = upload_course_pdf(course=course, pdf_file=uploaded)

        self.assertEqual(upload_type, "outline")
        self.assertIsNone(material)
        self.assertFalse(reused)
        upload_outline.assert_called_once_with(course=course, outline_file=uploaded)

    @patch("lessons.features.pdf_processing.use_cases.build_dag_from_outline")
    @patch("lessons.features.pdf_processing.use_cases.validate_course_outline_pdf")
    def test_same_outline_bytes_are_reused_without_resetting_approval(
        self,
        _validate_outline,
        build_outline,
    ):
        course = CourseGroup.objects.create(title="Science")
        pdf_bytes = b"%PDF-1.4 identical course outline"

        _course, first_reused = upload_course_outline(
            course=course,
            outline_file=SimpleUploadedFile("outline.pdf", pdf_bytes),
        )
        saved_outline = course.outlines.get()
        saved_outline.is_approved = True
        saved_outline.save(update_fields=["is_approved"])

        _course, second_reused = upload_course_outline(
            course=course,
            outline_file=SimpleUploadedFile("renamed-outline.pdf", pdf_bytes),
        )

        self.assertFalse(first_reused)
        self.assertTrue(second_reused)
        self.assertEqual(course.outlines.count(), 1)
        self.assertTrue(course.outlines.get().is_approved)
        build_outline.assert_called_once()

        course.outlines.get().outline_file.delete(save=False)

    @patch("lessons.features.pdf_processing.use_cases.build_dag_from_outline")
    @patch("lessons.features.pdf_processing.use_cases.validate_course_outline_pdf")
    def test_identical_outline_can_be_used_in_a_different_course(
        self,
        _validate_outline,
        _build_outline,
    ):
        first_course = CourseGroup.objects.create(title="Science A")
        second_course = CourseGroup.objects.create(title="Science B")
        pdf_bytes = b"%PDF-1.4 shared course outline"

        upload_course_outline(
            course=first_course,
            outline_file=SimpleUploadedFile("outline.pdf", pdf_bytes),
        )
        _course, reused = upload_course_outline(
            course=second_course,
            outline_file=SimpleUploadedFile("outline.pdf", pdf_bytes),
        )

        self.assertFalse(reused)
        self.assertEqual(CourseOutline.objects.count(), 2)

        for outline in CourseOutline.objects.all():
            outline.outline_file.delete(save=False)

    @patch("lessons.features.pdf_processing.use_cases.upload_learning_material")
    @patch("lessons.features.pdf_processing.use_cases.is_course_outline_pdf", return_value=False)
    def test_unified_upload_routes_detected_lesson_to_material_processing(
        self,
        _is_outline,
        upload_material,
    ):
        course = CourseGroup.objects.create(title="Science")
        expected = LearningMaterial(course=course, title="Matter lesson")
        upload_material.return_value = (expected, False)
        uploaded = SimpleUploadedFile("lesson.pdf", b"%PDF-1.4 lesson")

        upload_type, material, reused = upload_course_pdf(
            course=course,
            pdf_file=uploaded,
            title="Matter lesson",
        )

        self.assertEqual(upload_type, "lesson_material")
        self.assertIs(material, expected)
        self.assertFalse(reused)
        upload_material.assert_called_once_with(
            course=course,
            pdf_file=uploaded,
            title="Matter lesson",
        )

    def test_later_outline_merges_children_and_appends_new_roots(self):
        course = CourseGroup.objects.create(title="Science")
        matter = OutlineNode.objects.create(course=course, title="Matter", order=0, depth=0)
        solid = OutlineNode.objects.create(
            course=course,
            parent=matter,
            title="Solids",
            order=0,
            depth=1,
        )

        _merge_persisted_nodes(
            course,
            [
                ParsedOutlineNode(
                    title="Matter",
                    depth=0,
                    order=0,
                    children=[ParsedOutlineNode(title="Liquids", depth=1, order=0)],
                ),
                ParsedOutlineNode(title="Energy", depth=0, order=1),
            ],
        )

        matter.refresh_from_db()
        self.assertEqual(course.nodes.get(title="Matter").id, matter.id)
        self.assertEqual(course.nodes.get(title="Solids").id, solid.id)
        self.assertEqual(list(matter.children.values_list("title", flat=True)), ["Solids", "Liquids"])
        self.assertEqual(
            list(course.nodes.filter(parent__isnull=True).order_by("order").values_list("title", flat=True)),
            ["Matter", "Energy"],
        )

    def test_course_detail_lists_every_outline_source(self):
        course = CourseGroup.objects.create(title="Science")
        CourseOutline.objects.create(
            course=course,
            outline_file="outlines/part-1.pdf",
            is_approved=True,
        )
        CourseOutline.objects.create(
            course=course,
            outline_file="outlines/part-2.pdf",
            is_approved=False,
        )

        outline = CourseDetailSerializer(course).data["outline"]

        self.assertEqual(outline["source_count"], 2)
        self.assertEqual([item["filename"] for item in outline["files"]], ["part-1.pdf", "part-2.pdf"])
        self.assertFalse(outline["is_approved"])


class MilestoneModelSmokeTests(TestCase):
    def test_blank_image_description_is_excluded_from_narration(self):
        narration = build_narration_script_from_learning_objects(
            [
                {
                    "type": "image_description",
                    "title": "Grouping diagram",
                    "content": "",
                },
                {
                    "type": "lesson_content",
                    "title": "Properties",
                    "content": "Materials have observable properties.",
                },
            ]
        )

        self.assertEqual(len(narration), 1)
        self.assertEqual(narration[0]["title"], "Properties")

    @patch("lessons.services.audio_generator.synthesize_text_to_audio")
    def test_existing_playlist_skips_item_with_blank_narration(self, synthesize_audio):
        from django.conf import settings
        from pathlib import Path

        course = CourseGroup.objects.create(title="Science 7")
        material = LearningMaterial.objects.create(
            course=course,
            title="Properties",
            pdf_file="learning_materials/properties.pdf",
            generated_json={
                "narration_script": [
                    {"order": 1, "type": "image_description", "content": ""},
                    {"order": 2, "type": "lesson_content", "content": "Materials have properties."},
                ],
                "lesson_playlist": [
                    {"order": 0, "title": "Diagram", "narration_item_order": 1},
                    {"order": 1, "title": "Properties", "narration_item_order": 2},
                ],
            },
        )
        synthesize_audio.return_value = Path(settings.MEDIA_ROOT) / "audio_lessons" / "properties.mp3"

        result = generate_material_audio_playlist(material)

        material.refresh_from_db()
        self.assertEqual(result["generated_count"], 1)
        self.assertEqual(len(material.generated_json["lesson_playlist"]), 1)
        self.assertEqual(material.generated_json["lesson_playlist"][0]["title"], "Properties")
        synthesize_audio.assert_called_once()

    def test_course_outline_material_and_learning_object_scope(self):
        course = CourseGroup.objects.create(title="Science 7", description="Matter lessons")
        module = OutlineNode.objects.create(course=course, title="Matter", order=0, depth=0)
        topic = OutlineNode.objects.create(course=course, parent=module, title="States of Matter", order=0, depth=1)
        material = LearningMaterial.objects.create(
            course=course,
            outline_node=topic,
            module_node=module,
            title="States of Matter PDF",
            pdf_file="learning_materials/states.pdf",
            extracted_text="Solid, liquid, and gas are states of matter.",
            generated_json={
                "learning_objects": [],
                "image_descriptions": [],
                "lesson_playlist": [],
            },
            status=LearningMaterial.Status.COMPLETED,
        )
        learning_object = LearningObject.objects.create(
            material=material,
            kind=LearningObject.Kind.TEXT,
            title="Solid definition",
            content="A solid has a definite shape and volume.",
            order=0,
        )

        self.assertEqual(course.nodes.count(), 2)
        self.assertEqual(material.learning_objects.get(), learning_object)
        self.assertEqual(material.generated_json["lesson_playlist"], [])

    def test_material_serializer_removes_missing_audio_urls(self):
        course = CourseGroup.objects.create(title="Science 7")
        material = LearningMaterial.objects.create(
            course=course,
            title="Liquids",
            pdf_file="learning_materials/liquids.pdf",
            generated_json={
                "audio_playlist_generated": True,
                "lesson_playlist": [
                    {
                        "order": 0,
                        "title": "What is a Liquid?",
                        "type": "lesson",
                        "audio_url": "/media/audio_lessons/material_999/missing.wav",
                        "audio_file": "audio_lessons/material_999/missing.wav",
                        "audio_status": "generated",
                    }
                ],
            },
            status=LearningMaterial.Status.COMPLETED,
        )

        data = LearningMaterialSerializer(material).data
        item = data["generated_json"]["lesson_playlist"][0]

        self.assertNotIn("audio_url", item)
        self.assertNotIn("audio_file", item)
        self.assertEqual(item["audio_status"], "missing")
        self.assertFalse(data["generated_json"]["audio_playlist_generated"])


@patch.dict("os.environ", {"SEMANTIC_GROUPING_MODE": "legacy"})
class LearningResourceRelationshipTests(TestCase):
    """Regression coverage for the legacy lexical matcher.

    Semantic-mode routing has separate tests in test_semantic_grouping.py. Pinning
    this class avoids making its expected TF-IDF behavior depend on a developer's
    local .env rollout setting.
    """
    def setUp(self):
        self.client = authenticated_api_client()
        self.course = CourseGroup.objects.create(title="Science")
        self.node = OutlineNode.objects.create(
            course=self.course,
            title="Properties of Materials",
            order=0,
            depth=0,
        )

    def _material(self, title):
        return LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.node,
            module_node=self.node,
            title=title,
            pdf_file=f"learning_materials/{title}.pdf",
            status=LearningMaterial.Status.COMPLETED,
            generated_json={"learning_objects_confirmed": True},
        )

    def test_review_connections_is_blank_until_learning_objects_are_confirmed(self):
        draft = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.node,
            module_node=self.node,
            title="draft",
            pdf_file="learning_materials/draft.pdf",
            status=LearningMaterial.Status.COMPLETED,
            generated_json={"learning_objects_confirmed": False},
        )
        LearningObject.objects.create(
            material=draft,
            title="Solid",
            content="A solid has a definite shape and volume.",
        )
        ensure_learning_object_groups(draft)
        resources_url = (
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/learning-resources/"
        )

        before = self.client.get(resources_url)

        self.assertEqual(before.status_code, status.HTTP_200_OK)
        self.assertEqual(before.data["learning_object_groups"], [])
        self.assertEqual(before.data["question_pairings"], [])

        confirmed = self.client.post(
            f"/api/courses/{self.course.id}/materials/{draft.id}/confirm-learning-objects/",
            {},
            format="json",
        )
        after = self.client.get(resources_url)

        self.assertEqual(confirmed.status_code, status.HTTP_200_OK)
        self.assertEqual(len(after.data["learning_object_groups"]), 1)

    def test_teacher_can_add_a_manual_multiple_choice_question_to_a_topic(self):
        lesson = self._material("confirmed-lesson")
        LearningObject.objects.create(
            material=lesson,
            title="Solid",
            content="A solid has a definite shape and volume.",
        )
        ensure_learning_object_groups(lesson)

        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/questions/",
            {
                "prompt": "Which state of matter keeps its shape?",
                "question_type": "multiple_choice",
                "choices": ["Solid", "Liquid", "Gas", "Plasma"],
                "correct_answer": "Solid",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        question = Question.objects.get(prompt="Which state of matter keeps its shape?")
        self.assertEqual(question.question_type, Question.Type.MULTIPLE_CHOICE)
        self.assertEqual(question.choices, ["Solid", "Liquid", "Gas", "Plasma"])
        self.assertEqual(question.correct_answer, "Solid")
        self.assertEqual(question.material.generated_json["document_source"], "manual_questions")
        self.assertTrue(question.learning_object_links.exists())

    def test_manual_question_duplicate_is_rejected_within_topic(self):
        lesson = self._material("confirmed-lesson")
        LearningObject.objects.create(
            material=lesson,
            title="Matter",
            content="Matter has mass and occupies space.",
        )
        ensure_learning_object_groups(lesson)
        url = f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/questions/"
        payload = {
            "prompt": "1. What is matter?",
            "question_type": "true_false",
            "choices": ["True", "False"],
            "correct_answer": "True",
        }

        first = self.client.post(url, payload, format="json")
        payload["prompt"] = "What is matter"
        duplicate = self.client.post(url, payload, format="json")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(duplicate.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Question.objects.count(), 1)

    def test_uploaded_open_question_is_flagged_and_classified(self):
        payload = detected_question_payloads([{
            "block_id": 1,
            "page": 1,
            "category": "assessment",
            "text": "Why does a gas fill its container?",
        }])[0]

        self.assertEqual(payload["source_type"], Question.SourceType.PDF)
        self.assertEqual(payload["validation_status"], Question.ValidationStatus.NEEDS_REVIEW)
        self.assertTrue(payload["validation_issues"])
        self.assertIn(payload["thinking_order"], {"LOT", "HOT"})

    def test_editing_approved_question_updates_adaptive_bank(self):
        lesson = self._material("confirmed-lesson")
        learning_object = LearningObject.objects.create(
            material=lesson,
            title="Solid",
            content="A solid keeps its shape.",
        )
        ensure_learning_object_groups(lesson)
        question = Question.objects.create(
            material=lesson,
            prompt="Does a solid keep its shape?",
            question_type=Question.Type.OPEN_ENDED,
        )
        QuestionLearningObjectLink.objects.create(
            question=question,
            learning_object=learning_object,
            is_primary=True,
            review_status=QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED,
        )

        response = self.client.patch(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/questions/{question.id}/",
            {
                "prompt": question.prompt,
                "question_type": "true_false",
                "choices": ["True", "False"],
                "correct_answer": "True",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        question.refresh_from_db()
        self.assertEqual(question.validation_status, Question.ValidationStatus.READY)
        self.assertEqual(question.source_type, Question.SourceType.PDF)
        self.assertIsNotNone(question.adaptive_question_id)
        self.assertEqual(question.adaptive_question.thinking_order, question.thinking_order)

    def test_upload_rejects_a_topic_from_another_selected_module(self):
        other_module = OutlineNode.objects.create(
            course=self.course,
            title="Other module",
            order=1,
            depth=0,
        )
        topic = OutlineNode.objects.create(
            course=self.course,
            parent=self.node,
            title="States of matter",
            order=0,
            depth=1,
        )
        upload = SimpleUploadedFile("lesson.pdf", b"not-read-because-selection-is-invalid")

        with self.assertRaisesMessage(
            PdfProcessingUseCaseError,
            "Selected topic does not belong to the selected module.",
        ):
            upload_learning_material(
                course=self.course,
                pdf_file=upload,
                outline_node_id=topic.id,
                module_node_id=other_module.id,
            )

    @patch.dict(
        "os.environ",
        {
            "QUESTION_PAIR_LEXICAL_WEIGHT": "0.1",
            "QUESTION_PAIR_BLOCK_PROXIMITY_WEIGHT": "0.1",
            "QUESTION_PAIR_SAME_PAGE_WEIGHT": "0.2",
        },
    )
    def test_question_pairing_weights_are_configurable_and_normalized(self):
        weights = question_pairing_debug_configuration()["weights"]

        self.assertEqual(weights["lexical_tfidf"], 0.25)
        self.assertEqual(weights["source_block_proximity"], 0.25)
        self.assertEqual(weights["same_page"], 0.5)

    def test_related_but_not_high_confidence_content_waits_for_review(self):
        first_material = self._material("standard")
        second_material = self._material("alternate")
        first = LearningObject.objects.create(
            material=first_material,
            title="Definite shape",
            content="A rigid material keeps a definite shape.",
        )
        second = LearningObject.objects.create(
            material=second_material,
            title="Definite shape",
            content="A more detailed explanation of why the shape remains definite.",
        )

        ensure_learning_object_groups(first_material)
        ensure_learning_object_groups(second_material)
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertIsNotNone(first.group_id)
        self.assertNotEqual(first.group_id, second.group_id)
        suggestion = LearningObjectMatchSuggestion.objects.get(status="pending")
        self.assertEqual(suggestion.confidence, LearningObjectMatchSuggestion.Confidence.MEDIUM)
        self.assertFalse(hasattr(first.group, "difficulty"))

    def test_equivalent_wording_with_enough_content_support_is_auto_grouped(self):
        first_material = self._material("concise")
        second_material = self._material("detailed")
        first = LearningObject.objects.create(
            material=first_material,
            title="How rigid materials keep their shape",
            content="A rigid material keeps its shape when moved to another container.",
            order=0,
        )
        second = LearningObject.objects.create(
            material=second_material,
            title="Rigid materials and shape",
            content="Rigid materials retain their own shape even when their container changes.",
            order=0,
        )

        ensure_learning_object_groups(first_material)
        ensure_learning_object_groups(second_material)
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(first.group_id, second.group_id)
        suggestion = LearningObjectMatchSuggestion.objects.get(status="accepted")
        self.assertEqual(suggestion.confidence, LearningObjectMatchSuggestion.Confidence.HIGH)

    def test_high_content_match_is_automatically_connected(self):
        first_material = self._material("first-definition")
        second_material = self._material("second-definition")
        content = "A solid has a definite shape and volume because its particles are tightly packed."
        first = LearningObject.objects.create(
            material=first_material,
            title="Solid definition",
            content=content,
        )
        second = LearningObject.objects.create(
            material=second_material,
            title="Solid definition",
            content=content,
        )

        ensure_learning_object_groups(first_material)
        ensure_learning_object_groups(second_material)
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(first.group_id, second.group_id)
        suggestion = LearningObjectMatchSuggestion.objects.get()
        self.assertEqual(suggestion.confidence, LearningObjectMatchSuggestion.Confidence.HIGH)
        self.assertEqual(suggestion.status, LearningObjectMatchSuggestion.Status.ACCEPTED)

    def test_high_suggestion_between_existing_groups_is_automatically_connected(self):
        first_material = self._material("existing-first")
        second_material = self._material("existing-second")
        content = "A liquid has a fixed volume and takes the shape of its container."
        first = LearningObject.objects.create(
            material=first_material,
            group=LearningObjectGroup.objects.create(outline_node=self.node, label="Liquid A"),
            title="Liquid definition",
            content=content,
        )
        second = LearningObject.objects.create(
            material=second_material,
            group=LearningObjectGroup.objects.create(outline_node=self.node, label="Liquid B"),
            title="Liquid definition",
            content=content,
        )

        refresh_learning_object_match_suggestions(second_material)
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(first.group_id, second.group_id)
        suggestion = LearningObjectMatchSuggestion.objects.get()
        self.assertEqual(suggestion.confidence, LearningObjectMatchSuggestion.Confidence.HIGH)
        self.assertEqual(suggestion.status, LearningObjectMatchSuggestion.Status.ACCEPTED)

    def test_exact_title_alone_does_not_force_high_confidence(self):
        material = self._material("candidate")
        candidate = LearningObject.objects.create(
            material=material,
            title="Solid",
            content="This section lists playground games and outdoor activities.",
        )

        evidence = learning_object_match_evidence(
            "Solid (Part 2 of 3)",
            "A worked example calculates the travel time of a moving vehicle.",
            0,
            candidate,
        )

        self.assertTrue(evidence["exact_normalized_title"])
        self.assertLess(evidence["score"], 0.75)
        self.assertLess(evidence["content_support"], 0.45)

    @patch.dict(
        "os.environ",
        {
            "LEARNING_OBJECT_MATCH_AUTO_THRESHOLD": "0.40",
            "LEARNING_OBJECT_MATCH_REVIEW_THRESHOLD": "0.20",
            "LEARNING_OBJECT_MATCH_MINIMUM_MARGIN": "0.00",
            "LEARNING_OBJECT_MATCH_GROUP_MEMBER_THRESHOLD": "0.00",
            "LEARNING_OBJECT_MATCH_CONTENT_SUPPORT_THRESHOLD": "0.45",
        },
    )
    def test_shared_solid_title_does_not_connect_different_content(self):
        definition_material = self._material("definition")
        example_material = self._material("example")
        definition = LearningObject.objects.create(
            material=definition_material,
            title="Solid",
            content="A solid is matter with a definite shape and a definite volume.",
        )
        example = LearningObject.objects.create(
            material=example_material,
            title="Solid (Part 2 of 3)",
            content="Examples include a book on a shelf, an ice cube in a glass, and a rock on a table.",
        )

        ensure_learning_object_groups(definition_material)
        ensure_learning_object_groups(example_material)
        definition.refresh_from_db()
        example.refresh_from_db()

        self.assertNotEqual(definition.group_id, example.group_id)
        suggestion = LearningObjectMatchSuggestion.objects.get(status="pending")
        self.assertEqual(suggestion.confidence, LearningObjectMatchSuggestion.Confidence.MEDIUM)
        self.assertLess(suggestion.evidence["content_support"], 0.45)

    def test_different_concepts_on_the_same_topic_node_keep_separate_groups(self):
        first_material = self._material("shape-source")
        second_material = self._material("volume-source")
        shape = LearningObject.objects.create(
            material=first_material,
            title="Shape",
            content="Shape describes the outside form of an object.",
            order=0,
        )
        volume = LearningObject.objects.create(
            material=second_material,
            title="Volume",
            content="Volume measures how much space an object occupies.",
            order=0,
        )

        ensure_learning_object_groups(first_material)
        ensure_learning_object_groups(second_material)
        shape.refresh_from_db()
        volume.refresh_from_db()

        self.assertNotEqual(shape.group_id, volume.group_id)

    def test_same_pdf_objects_are_never_automatic_variation_candidates(self):
        material = self._material("one-source")
        first = LearningObject.objects.create(
            material=material,
            title="Solid",
            content="A solid has a definite shape.",
        )
        second = LearningObject.objects.create(
            material=material,
            title="Solid",
            content="A solid also has a definite volume.",
            order=1,
        )

        ensure_learning_object_groups(material)
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertNotEqual(first.group_id, second.group_id)
        self.assertFalse(LearningObjectMatchSuggestion.objects.exists())

    def test_hybrid_match_evidence_exposes_each_score_component(self):
        material = self._material("evidence")
        candidate = LearningObject.objects.create(
            material=material,
            title="Properties of solids",
            content="Solid particles stay close together and keep their shape.",
            section_title="States of matter",
            order=2,
        )

        evidence = learning_object_match_evidence(
            "Solid properties",
            "A solid keeps its shape because its particles are closely packed.",
            2,
            candidate,
            section_title="States of matter",
        )

        self.assertEqual(
            set(evidence),
            {
                "score",
                "title_tfidf",
                "content_tfidf",
                "character_ngram",
                "keyword_overlap",
                "structure",
                "content_support",
                "exact_normalized_title",
            },
        )
        self.assertGreater(evidence["score"], 0)

    @patch.dict(
        "os.environ",
        {
            "LEARNING_OBJECT_MATCH_AUTO_THRESHOLD": "0.99",
            "LEARNING_OBJECT_MATCH_REVIEW_THRESHOLD": "0.10",
        },
    )
    def test_medium_confidence_match_is_suggested_instead_of_auto_grouped(self):
        first_material = self._material("first-version")
        second_material = self._material("second-version")
        first = LearningObject.objects.create(
            material=first_material,
            title="Solid",
            content="A solid keeps its own shape.",
        )
        second = LearningObject.objects.create(
            material=second_material,
            title="Solid",
            content="A solid has a definite shape and volume.",
        )

        ensure_learning_object_groups(first_material)
        ensure_learning_object_groups(second_material)
        first.refresh_from_db()
        second.refresh_from_db()
        suggestion = LearningObjectMatchSuggestion.objects.get()

        self.assertNotEqual(first.group_id, second.group_id)
        self.assertEqual(suggestion.status, LearningObjectMatchSuggestion.Status.PENDING)
        self.assertEqual(suggestion.confidence, LearningObjectMatchSuggestion.Confidence.MEDIUM)
        self.assertEqual(suggestion.evidence["method"], "hybrid_tfidf_v3_content_guard")

    @patch.dict(
        "os.environ",
        {
            "LEARNING_OBJECT_MATCH_AUTO_THRESHOLD": "0.50",
            "LEARNING_OBJECT_MATCH_REVIEW_THRESHOLD": "0.10",
            "LEARNING_OBJECT_MATCH_MINIMUM_MARGIN": "0.20",
        },
    )
    def test_ambiguous_equal_group_winners_are_not_automatically_connected(self):
        first_material = self._material("first")
        second_material = self._material("second")
        source_material = self._material("source")
        first_group = LearningObjectGroup.objects.create(outline_node=self.node, label="Solid A")
        second_group = LearningObjectGroup.objects.create(outline_node=self.node, label="Solid B")
        LearningObject.objects.create(
            material=first_material,
            group=first_group,
            title="Solid",
            content="A solid keeps its shape.",
        )
        LearningObject.objects.create(
            material=second_material,
            group=second_group,
            title="Solid",
            content="A solid keeps its shape.",
        )
        source = LearningObject.objects.create(
            material=source_material,
            title="Solid",
            content="A solid keeps its shape.",
        )

        ensure_learning_object_groups(source_material)
        source.refresh_from_db()

        self.assertNotIn(source.group_id, {first_group.id, second_group.id})
        suggestion = LearningObjectMatchSuggestion.objects.get(status="pending")
        self.assertEqual(suggestion.evidence["winner_margin"], 0.0)

    def test_detected_question_is_separate_and_paired_to_best_learning_object(self):
        material = self._material("questions")
        solid = LearningObject.objects.create(
            material=material,
            title="Definite shape",
            content="A rigid material keeps its own shape.",
            source_page=1,
            source_block_id=2,
            order=0,
        )
        LearningObject.objects.create(
            material=material,
            title="Flow",
            content="A fluid can move and take the shape of a container.",
            source_page=2,
            source_block_id=20,
            order=1,
        )
        ensure_learning_object_groups(material)

        synchronize_detected_questions(
            material,
            [
                {
                    "block_id": 4,
                    "page": 1,
                    "category": "assessment",
                    "text": "Which material keeps its own definite shape?",
                },
                {
                    "block_id": 5,
                    "page": 1,
                    "category": "assessment",
                    "text": "Expected answer:\nThe rigid material.",
                },
            ],
        )

        self.assertEqual(Question.objects.count(), 1)
        question = Question.objects.get()
        link = QuestionLearningObjectLink.objects.get(question=question)
        self.assertEqual(link.learning_object, solid)
        self.assertTrue(link.is_primary)
        self.assertEqual(link.method, "layout_tfidf")
        self.assertNotIn(question.prompt, solid.content)

    def test_question_only_pdf_pairs_against_learning_objects_in_its_topic(self):
        lesson_material = self._material("lesson")
        question_material = self._material("assessment")
        solid = LearningObject.objects.create(
            material=lesson_material,
            title="Solid shape",
            content="A solid has a definite shape and keeps its form inside a container.",
            order=0,
        )
        LearningObject.objects.create(
            material=lesson_material,
            title="Liquid shape",
            content="A liquid flows and takes the shape of its container.",
            order=1,
        )
        ensure_learning_object_groups(lesson_material)
        question = Question.objects.create(
            material=question_material,
            prompt="Which state of matter has a definite shape and keeps its form?",
        )

        refresh_question_learning_object_links(question_material)
        link = QuestionLearningObjectLink.objects.get(question=question)

        self.assertEqual(link.learning_object, solid)
        self.assertEqual(link.method, "topic_tfidf")
        self.assertIn(
            link.review_status,
            {
                QuestionLearningObjectLink.ReviewStatus.AUTO_CONFIRMED,
                QuestionLearningObjectLink.ReviewStatus.PENDING_REVIEW,
            },
        )

    def test_question_detector_keeps_choices_but_removes_supplied_answer(self):
        payloads = detected_question_payloads(
            [
                {
                    "block_id": 9,
                    "page": 3,
                    "category": "assessment",
                    "text": "Which description is correct?\nA. First choice\nB. Second choice\nAnswer: First choice",
                }
            ]
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["prompt"], "Which description is correct?")
        self.assertEqual(payloads[0]["question_type"], Question.Type.MULTIPLE_CHOICE)
        self.assertEqual(payloads[0]["choices"], ["First choice", "Second choice"])
        self.assertEqual(payloads[0]["correct_answer"], "First choice")
        self.assertNotIn("Answer:", payloads[0]["prompt"])

    def test_question_detector_splits_separate_prompts_but_joins_wrapped_lines(self):
        payloads = detected_question_payloads(
            [
                {
                    "block_id": 3,
                    "page": 1,
                    "category": "assessment",
                    "text": "What stays unchanged?\nWhy does it stay unchanged?",
                },
                {
                    "block_id": 4,
                    "page": 1,
                    "category": "assessment",
                    "text": "Q: What happens when the temperature\nbecomes lower?",
                },
            ]
        )

        self.assertEqual(len(payloads), 3)
        self.assertEqual(payloads[0]["prompt"], "What stays unchanged?")
        self.assertEqual(payloads[1]["prompt"], "Why does it stay unchanged?")
        self.assertEqual(
            payloads[2]["prompt"],
            "Q: What happens when the temperature becomes lower?",
        )

    def test_learning_resources_endpoint_exposes_groups_and_question_pairs(self):
        material = self._material("paired")
        learning_object = LearningObject.objects.create(
            material=material,
            title="Observable property",
            content="An observable property can be noticed or measured.",
            source_page=1,
            source_block_id=2,
        )
        ensure_learning_object_groups(material)
        synchronize_detected_questions(
            material,
            [
                {
                    "block_id": 3,
                    "page": 1,
                    "category": "assessment",
                    "text": "Which property can be noticed or measured?",
                }
            ],
        )

        response = self.client.get(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/learning-resources/"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        group = response.data["learning_object_groups"][0]
        self.assertEqual(group["learning_objects"][0]["id"], learning_object.id)
        self.assertEqual(
            group["questions"][0]["learning_object_links"][0]["learning_object"],
            learning_object.id,
        )

    @patch.dict(
        "os.environ",
        {
            "QUESTION_PAIR_AUTO_THRESHOLD": "0.99",
            "QUESTION_PAIR_REVIEW_THRESHOLD": "0.00",
        },
    )
    def test_uncertain_question_waits_for_teacher_confirmation(self):
        material = self._material("question-review")
        learning_object = LearningObject.objects.create(
            material=material,
            title="Solids",
            content="A solid keeps its own shape.",
            source_page=1,
            source_block_id=2,
        )
        ensure_learning_object_groups(material)
        synchronize_detected_questions(
            material,
            [
                {
                    "block_id": 10,
                    "page": 1,
                    "category": "assessment",
                    "text": "What happens to the shape of a solid?",
                }
            ],
        )
        question = Question.objects.get(material=material)
        link = QuestionLearningObjectLink.objects.get(question=question)

        self.assertEqual(link.review_status, QuestionLearningObjectLink.ReviewStatus.PENDING_REVIEW)
        self.assertFalse(link.is_primary)

        response = self.client.get(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/learning-resources/"
        )
        self.assertEqual(response.data["learning_object_groups"][0]["questions"], [])
        self.assertEqual(response.data["question_pairings"][0]["id"], question.id)

        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/questions/{question.id}/pairing/",
            {"decision": "confirm"},
            format="json",
        )
        link.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(link.learning_object, learning_object)
        self.assertEqual(link.review_status, QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED)
        self.assertTrue(link.is_primary)
        self.assertEqual(response.data["learning_object_groups"][0]["questions"][0]["id"], question.id)

    def test_teacher_can_change_a_question_to_another_concept_group(self):
        material = self._material("question-change")
        first = LearningObject.objects.create(
            material=material,
            title="Solid",
            content="A solid has a definite shape.",
            order=0,
        )
        second = LearningObject.objects.create(
            material=material,
            title="Liquid",
            content="A liquid takes the shape of its container.",
            order=1,
        )
        ensure_learning_object_groups(material)
        first.refresh_from_db()
        second.refresh_from_db()
        question = Question.objects.create(material=material, prompt="Which state takes the container's shape?")
        QuestionLearningObjectLink.objects.create(
            question=question,
            learning_object=first,
            relevance_score=0.35,
            is_primary=False,
            review_status=QuestionLearningObjectLink.ReviewStatus.PENDING_REVIEW,
        )

        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/questions/{question.id}/pairing/",
            {"decision": "change", "learning_object_group_id": second.group_id},
            format="json",
        )
        link = QuestionLearningObjectLink.objects.get(question=question)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(link.learning_object.group_id, second.group_id)
        self.assertEqual(link.method, "teacher_selected")
        self.assertEqual(link.relevance_score, 0.0)
        self.assertEqual(link.review_status, QuestionLearningObjectLink.ReviewStatus.TEACHER_CONFIRMED)
        self.assertTrue(link.is_primary)

    def test_teacher_can_leave_a_question_unpaired(self):
        material = self._material("question-unpaired")
        learning_object = LearningObject.objects.create(
            material=material,
            title="Matter",
            content="Matter takes up space.",
        )
        ensure_learning_object_groups(material)
        question = Question.objects.create(material=material, prompt="What is matter?")
        link = QuestionLearningObjectLink.objects.create(
            question=question,
            learning_object=learning_object,
            relevance_score=0.20,
            is_primary=False,
            review_status=QuestionLearningObjectLink.ReviewStatus.UNMATCHED,
        )

        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/questions/{question.id}/pairing/",
            {"decision": "unpair"},
            format="json",
        )
        link.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(link.review_status, QuestionLearningObjectLink.ReviewStatus.TEACHER_UNPAIRED)
        self.assertFalse(link.is_primary)
        self.assertEqual(response.data["learning_object_groups"][0]["questions"], [])

    def test_connect_endpoint_groups_different_titles_without_classifying_them(self):
        first_material = self._material("first")
        second_material = self._material("second")
        first = LearningObject.objects.create(
            material=first_material,
            title="Shape",
            content="Materials have different shapes.",
        )
        second = LearningObject.objects.create(
            material=second_material,
            title="Understanding an object's shape",
            content="A longer explanation of the same concept.",
        )
        ensure_learning_object_groups(first_material)
        ensure_learning_object_groups(second_material)

        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/connect-learning-objects/",
            {"learning_object_ids": [first.id, second.id], "label": "Shape concept"},
            format="json",
        )
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(first.group_id, second.group_id)
        self.assertEqual(first.group.label, "Shape concept")

    def test_teacher_can_accept_a_pending_match_suggestion(self):
        first_material = self._material("first")
        second_material = self._material("second")
        first = LearningObject.objects.create(
            material=first_material,
            group=LearningObjectGroup.objects.create(outline_node=self.node, label="Shape"),
            title="Shape",
            content="A solid keeps its shape.",
        )
        second = LearningObject.objects.create(
            material=second_material,
            group=LearningObjectGroup.objects.create(outline_node=self.node, label="Form"),
            title="Form of a solid",
            content="Rigid matter retains its form.",
        )
        suggestion = LearningObjectMatchSuggestion.objects.create(
            outline_node=self.node,
            source_learning_object=first,
            candidate_learning_object=second,
            similarity_score=0.48,
            confidence=LearningObjectMatchSuggestion.Confidence.MEDIUM,
            evidence={"method": "hybrid_tfidf_v2"},
        )

        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/match-suggestions/{suggestion.id}/accept/",
            format="json",
        )
        first.refresh_from_db()
        second.refresh_from_db()
        suggestion.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(first.group_id, second.group_id)
        self.assertEqual(suggestion.status, LearningObjectMatchSuggestion.Status.ACCEPTED)
        self.assertEqual(
            suggestion.confidence,
            LearningObjectMatchSuggestion.Confidence.TEACHER_CONFIRMED,
        )
        self.assertEqual(response.data["match_suggestions"], [])

    def test_teacher_can_reject_a_pending_match_suggestion(self):
        first_material = self._material("first")
        second_material = self._material("second")
        first = LearningObject.objects.create(
            material=first_material,
            group=LearningObjectGroup.objects.create(outline_node=self.node, label="Shape"),
            title="Shape",
            content="A solid keeps its shape.",
        )
        second = LearningObject.objects.create(
            material=second_material,
            group=LearningObjectGroup.objects.create(outline_node=self.node, label="Form"),
            title="Form of a solid",
            content="Rigid matter retains its form.",
        )
        suggestion = LearningObjectMatchSuggestion.objects.create(
            outline_node=self.node,
            source_learning_object=first,
            candidate_learning_object=second,
            similarity_score=0.48,
            confidence=LearningObjectMatchSuggestion.Confidence.MEDIUM,
            evidence={"method": "hybrid_tfidf_v2"},
        )

        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/match-suggestions/{suggestion.id}/reject/",
            format="json",
        )
        first.refresh_from_db()
        second.refresh_from_db()
        suggestion.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotEqual(first.group_id, second.group_id)
        self.assertEqual(suggestion.status, LearningObjectMatchSuggestion.Status.REJECTED)
        self.assertEqual(response.data["match_suggestions"], [])

    def test_separate_endpoint_moves_one_object_to_a_singleton_group(self):
        first_material = self._material("first")
        second_material = self._material("second")
        first = LearningObject.objects.create(
            material=first_material,
            title="Shape",
            content="A solid keeps its shape.",
        )
        second = LearningObject.objects.create(
            material=second_material,
            title="Shape explained",
            content="A longer explanation about shape.",
        )
        ensure_learning_object_groups(first_material)
        first.refresh_from_db()
        second.group = first.group
        second.save(update_fields=["group"])
        original_group_id = first.group_id

        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/separate-learning-object/",
            {"learning_object_id": second.id},
            format="json",
        )
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(first.group_id, original_group_id)
        self.assertNotEqual(second.group_id, original_group_id)
        self.assertEqual(second.group.label, second.title)
        self.assertEqual(second.group.learning_objects.count(), 1)

    def test_material_serializer_hides_structural_metadata_learning_objects(self):
        course = CourseGroup.objects.create(title="Science 7")
        material = LearningMaterial.objects.create(
            course=course,
            title="Properties PDF",
            pdf_file="learning_materials/properties.pdf",
            status=LearningMaterial.Status.COMPLETED,
        )
        LearningObject.objects.create(
            material=material,
            kind=LearningObject.Kind.TEXT,
            title="Module 1",
            content="Properties of Matter",
            order=0,
        )
        LearningObject.objects.create(
            material=material,
            kind=LearningObject.Kind.TEXT,
            title="Matter",
            content="Matter is anything that has mass and occupies space.",
            order=1,
        )

        data = LearningMaterialSerializer(material).data

        self.assertEqual([item["title"] for item in data["learning_objects"]], ["Matter"])

    def test_publish_topic_requires_a_confirmed_material(self):
        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/publish/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.node.refresh_from_db()
        self.assertFalse(self.node.published)

    @patch(
        "lessons.views.settle_group",
        return_value={
            "representative_id": None,
            "assigned": [],
            "needs_confirmation": [],
            "extras": 0,
            "generated": ["SIMPLIFIED", "ELABORATED"],
            "errors": [],
        },
    )
    @patch("lessons.services.audio_generator.synthesize_text_to_audio")
    def test_publish_topic_generates_audio_and_marks_the_node_published(
        self, synthesize_audio, settle_group_mock
    ):
        from django.conf import settings
        from pathlib import Path

        material = self._material("confirmed-lesson")
        material.generated_json = {
            **material.generated_json,
            "narration_script": [
                {"order": 1, "type": "lesson_content", "content": "Matter has mass and volume."},
            ],
            "lesson_playlist": [
                {"order": 0, "title": "Matter", "narration_item_order": 1},
            ],
        }
        material.save(update_fields=["generated_json"])
        LearningObject.objects.create(
            material=material,
            title="Matter",
            content="Matter has mass and volume.",
        )
        ensure_learning_object_groups(material)
        synthesize_audio.return_value = Path(settings.MEDIA_ROOT) / "audio_lessons" / "matter.mp3"

        response = self.client.post(
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/publish/",
            {},
            format="json",
        )

        self.node.refresh_from_db()
        material.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(self.node.published)
        self.assertIsNotNone(self.node.published_at)
        self.assertEqual(response.data["publish"]["audio_generated_count"], 1)
        self.assertEqual(response.data["publish"]["adaptive_variants_generated"], 2)
        self.assertTrue(material.generated_json["lesson_audio_generated"])
        synthesize_audio.assert_called_once()
        # Publish now settles each group in the topic rather than calling the
        # standalone generator once for the node.
        settle_group_mock.assert_called_once()

    def test_course_outline_upload_rejects_non_pdf(self):
        client = authenticated_api_client()
        course = CourseGroup.objects.create(title="Science 7")
        response = client.post(
            f"/api/courses/{course.id}/upload-outline/",
            {"outline_file": SimpleUploadedFile("outline.txt", b"Module 1: Matter")},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], "Only PDF course outlines are supported.")


class ConfirmLearningObjectsTests(TestCase):
    def test_learning_object_edit_updates_database_and_material_snapshot(self):
        client = authenticated_api_client()
        course = CourseGroup.objects.create(title="Science 7")
        material = LearningMaterial.objects.create(
            course=course,
            title="Matter PDF",
            pdf_file=SimpleUploadedFile("matter.pdf", b"%PDF-1.4"),
            generated_json={
                "learning_objects": [],
                "lesson_playlist": [
                    {
                        "order": 0,
                        "title": "Old title",
                        "text": "Old content",
                        "audio_url": "/media/audio/old.wav",
                    }
                ],
                "audio_playlist_generated": True,
                "lesson_audio_generated": True,
                "learning_objects_confirmed": True,
            },
            status=LearningMaterial.Status.COMPLETED,
        )
        learning_object = LearningObject.objects.create(
            material=material,
            kind=LearningObject.Kind.TEXT,
            title="Old title",
            content="Old content",
            order=0,
        )

        response = client.patch(
            f"/api/courses/{course.id}/materials/{material.id}/learning-objects/{learning_object.id}/",
            {"title": "Updated title", "content": "Updated content"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        learning_object.refresh_from_db()
        material.refresh_from_db()
        self.assertEqual(learning_object.title, "Updated title")
        self.assertEqual(learning_object.content, "Updated content")
        self.assertEqual(material.generated_json["learning_objects"][0]["title"], "Updated title")
        self.assertEqual(material.generated_json["learning_objects"][0]["content"], "Updated content")
        self.assertEqual(
            material.generated_json["learning_objects"][0]["learning_object_id"],
            learning_object.id,
        )
        self.assertFalse(material.generated_json["learning_objects_confirmed"])
        self.assertFalse(material.generated_json["audio_playlist_generated"])
        self.assertNotIn("audio_url", material.generated_json["lesson_playlist"][0])

    def test_learning_object_delete_updates_database_and_material_snapshot(self):
        client = authenticated_api_client()
        course = CourseGroup.objects.create(title="Science 7")
        material = LearningMaterial.objects.create(
            course=course,
            title="Matter PDF",
            pdf_file=SimpleUploadedFile("matter.pdf", b"%PDF-1.4"),
            generated_json={"learning_objects": [], "learning_objects_confirmed": True},
            status=LearningMaterial.Status.COMPLETED,
        )
        first = LearningObject.objects.create(
            material=material,
            kind=LearningObject.Kind.TEXT,
            title="Keep me",
            content="This row stays.",
            order=0,
        )
        removed = LearningObject.objects.create(
            material=material,
            kind=LearningObject.Kind.TEXT,
            title="Delete me",
            content="This row is removed.",
            order=1,
        )

        response = client.delete(
            f"/api/courses/{course.id}/materials/{material.id}/learning-objects/{removed.id}/",
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        material.refresh_from_db()
        self.assertFalse(LearningObject.objects.filter(id=removed.id).exists())
        self.assertEqual(list(material.learning_objects.values_list("id", flat=True)), [first.id])
        self.assertEqual(len(material.generated_json["learning_objects"]), 1)
        self.assertEqual(material.generated_json["learning_objects"][0]["learning_object_id"], first.id)
        self.assertEqual(material.generated_json["learning_objects"][0]["title"], "Keep me")
        self.assertFalse(material.generated_json["learning_objects_confirmed"])

    def test_image_learning_object_keeps_type_and_image_url_after_edit_and_confirm(self):
        client = authenticated_api_client()
        course = CourseGroup.objects.create(title="Science 7")
        material = LearningMaterial.objects.create(
            course=course,
            title="Water Cycle PDF",
            pdf_file=SimpleUploadedFile("water-cycle.pdf", b"%PDF-1.4"),
            generated_json={},
            status=LearningMaterial.Status.COMPLETED,
        )
        learning_object = LearningObject.objects.create(
            material=material,
            kind=LearningObject.Kind.IMAGE,
            title="Water cycle diagram",
            content="Image content",
            image_url="/media/extracted_images/water-cycle.png",
            order=0,
        )

        response = client.patch(
            f"/api/courses/{course.id}/materials/{material.id}/learning-objects/{learning_object.id}/",
            {"content": "Teacher description of evaporation, condensation, and precipitation."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        learning_object.refresh_from_db()
        self.assertEqual(learning_object.kind, LearningObject.Kind.IMAGE)
        self.assertEqual(learning_object.image_url, "/media/extracted_images/water-cycle.png")

        response = client.post(
            f"/api/courses/{course.id}/materials/{material.id}/confirm-learning-objects/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        material.refresh_from_db()
        snapshot = material.generated_json["learning_objects"][0]
        self.assertEqual(snapshot["type"], "image_description")
        self.assertEqual(snapshot["kind"], LearningObject.Kind.IMAGE)
        self.assertEqual(snapshot["image_url"], "/media/extracted_images/water-cycle.png")

    def test_confirm_learning_objects_saves_reviewed_content_only(self):
        client = authenticated_api_client()
        course = CourseGroup.objects.create(title="Science 7")
        material = LearningMaterial.objects.create(
            course=course,
            title="States of Matter PDF",
            pdf_file=SimpleUploadedFile("states.pdf", b"%PDF-1.4"),
            generated_json={},
            status=LearningMaterial.Status.COMPLETED,
        )
        LearningObject.objects.create(
            material=material,
            kind=LearningObject.Kind.TEXT,
            title="Matter",
            content="Matter is anything that has mass and occupies space.",
            order=0,
        )

        response = client.post(
            f"/api/courses/{course.id}/materials/{material.id}/confirm-learning-objects/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        material.refresh_from_db()
        self.assertTrue(material.generated_json.get("learning_objects_confirmed"))
        self.assertNotIn("questions_generated", material.generated_json)


class OutlineParserTests(TestCase):
    def _titles(self, nodes):
        return [(node.title, [child.title for child in node.children]) for node in nodes]

    def test_document_role_classifier_accepts_outline_structure(self):
        text = """
        Weekly Course Outline
        Module 1: Foundations
        Lesson 1: Introduction
        Lesson 2: Core Ideas
        Module 2: Applications
        Lesson 1: Guided Practice
        """

        self.assertTrue(is_course_outline_document(text))

    def test_document_role_classifier_rejects_lesson_content_with_one_lesson_heading(self):
        text = """
        Lesson 1: A Scientific Concept
        A concept is introduced through a clear definition and an explanation of its properties.
        Definition
        The concept describes an observable phenomenon that learners can examine in daily life.
        Examples
        Learners can observe several examples and compare how their properties differ.
        Quick Check
        What did you observe?
        """

        self.assertFalse(is_course_outline_document(text))

    def test_document_role_classifier_recognizes_curriculum_guide_schema(self):
        text = """
        Content Standards
        Learning Competencies
        First Grading Period
        1.1 Observable characteristics
        1.2 Classification methods
        2.1 Changes over time
        """

        self.assertTrue(is_course_outline_document(text))

    def test_teacher_module_bullets_stay_under_declared_module(self):
        text = """
        Weekly Course Outline
        Week 2
        Module 1: Matter
        • States of Matter
        • Properties of Matter
        Week 3
        Module 1: Matter
        • Physical Properties
        • Chemical Properties
        Week 4
        Module 2: Mixtures
        • Homogeneous Mixtures
        • Heterogeneous Mixtures
        Week 5
        Module 2: Separating Mixtures
        • Filtration
        • Evaporation
        Week 6
        Module 3: Force and Motion
        • Push and Pull
        • Balanced and Unbalanced Forces
        """

        nodes = parse_outline_text(text)

        self.assertEqual(
            self._titles(nodes),
            [
                (
                    "Matter",
                    [
                        "States of Matter",
                        "Properties of Matter",
                        "Physical Properties",
                        "Chemical Properties",
                    ],
                ),
                (
                    "Mixtures",
                    [
                        "Homogeneous Mixtures",
                        "Heterogeneous Mixtures",
                        "Separating Mixtures",
                        "Filtration",
                        "Evaporation",
                    ],
                ),
                (
                    "Force and Motion",
                    ["Push and Pull", "Balanced and Unbalanced Forces"],
                ),
            ],
        )

    def test_wrapped_bullet_title_does_not_absorb_learning_focus(self):
        text = """
        Week 3
        Module 1: Matter
        • Physical and Chemical
        Changes
        Differentiate physical and chemical changes.
        Concept mapping
        Week 4
        Module 2: Mixtures
        • Homogeneous Mixtures
        """

        nodes = parse_outline_text(text)

        self.assertEqual(nodes[0].title, "Matter")
        self.assertEqual([child.title for child in nodes[0].children], ["Physical and Chemical Changes"])
        self.assertEqual(nodes[1].title, "Mixtures")

    def test_pdf_extracted_wrapped_outline_titles_merge_correctly(self):
        text = """
        Week 2
        Module 1: Properties of Matter
        * Lesson 1: Solid, Liquid and
        Gas
        * Lesson 2: Grouping Materials
        Based on Properties
        Week 3
        Module 1: Properties of Matter
        * Lesson 3: Physical and
        Chemical Properties of
        Matter: Useful and Harmful
        Materials
        * Lesson 4: Mixtures and
        Their Characteristics
        """

        nodes = parse_outline_text(text)

        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].title, "Properties of Matter")
        self.assertEqual(
            [child.title for child in nodes[0].children],
            [
                "Solid, Liquid and Gas",
                "Grouping Materials Based on Properties",
                "Physical and Chemical Properties of Matter: Useful and Harmful Materials",
                "Mixtures and Their Characteristics",
            ],
        )

    def test_learning_objectives_and_lesson_titles_are_excluded_from_learning_objects(self):
        classified_blocks = [
            {
                "block_id": 1,
                "page": 1,
                "text": "Lesson 1: Solid, Liquid and Gas",
                "line_count": 1,
                "category": "document_metadata",
                "include_in_narration": False,
            },
            {
                "block_id": 2,
                "page": 1,
                "text": "Learning Objectives",
                "line_count": 1,
                "category": "document_metadata",
                "include_in_narration": False,
            },
            {
                "block_id": 3,
                "page": 1,
                "text": "Describe how particles behave in solids, liquids, and gases.",
                "line_count": 1,
                "category": "lesson_content",
                "include_in_narration": True,
            },
            {
                "block_id": 4,
                "page": 1,
                "text": "Practice appropriate ways of protecting important body parts.",
                "line_count": 1,
                "category": "lesson_content",
                "include_in_narration": True,
            },
            {
                "block_id": 5,
                "page": 1,
                "text": "Matter is anything that has mass and occupies space.",
                "line_count": 2,
                "category": "lesson_content",
                "include_in_narration": True,
            },
            {
                "block_id": 6,
                "page": 1,
                "text": "A solid has a definite shape, a liquid flows, and a gas expands to fill its container.",
                "line_count": 2,
                "category": "lesson_content",
                "include_in_narration": True,
            },
        ]

        learning_objects = build_section_learning_objects(classified_blocks, [])
        all_content = "\n".join(item["content"] for item in learning_objects)

        self.assertNotIn("Lesson 1: Solid, Liquid and Gas", all_content)
        self.assertNotIn("Learning Objectives", all_content)
        self.assertNotIn("Describe how particles behave in solids, liquids, and gases.", all_content)
        self.assertNotIn("Practice appropriate ways of protecting important body parts.", all_content)
        self.assertIn("Matter is anything that has mass and occupies space.", all_content)
        self.assertIn("A solid has a definite shape, a liquid flows, and a gas expands to fill its container.", all_content)

    def test_related_info_cleanup_preserves_teacher_context(self):
        related_info = _clean_related_info(
            {
                "Learning Focus": [" Describe solids and liquids. ", ""],
                "Activities": ["Laboratory observation"],
                "Empty": "",
            }
        )

        self.assertEqual(
            related_info,
            {
                "learning_focus": ["Describe solids and liquids."],
                "activities": ["Laboratory observation"],
            },
        )

    def test_deped_content_column_extracts_only_numbered_topics(self):
        text = """
        PDF CONTENT COLUMN
        PAGE 1
        Grade 4 - Matter
        FIRST QUARTER/FIRST GRADING PERIOD
        1. Properties
        1.1. Properties used to
        group and store
        materials
        1.2. Importance of
        interpreting product
        labels
        1.3. Proper disposal of
        waste
        PAGE 2
        1. Properties
        1.1. Properties used to
        group and store
        materials
        1.2. Importance of
        interpreting product
        labels
        Proper disposal of
        waste
        2. Changes that Materials
        Undergo
        2.1. Changes that are
        useful
        2.2. Changes that are
        harmful
        PAGE 3
        Parts and Functions
        1. Humans
        1.1 Major organs of the
        body
        PAGE 5
        2. Animals
        2.1
        Live on land
        or in water
        RAW PDF TEXT
        1. DLP 26.
        2. Stirring rod
        """

        nodes = parse_outline_text(text)

        self.assertEqual(
            self._titles(nodes),
            [
                (
                    "Properties",
                    [
                        "Properties used to group and store materials",
                        "Importance of interpreting product labels",
                        "Proper disposal of waste",
                    ],
                ),
                (
                    "Changes that Materials Undergo",
                    ["Changes that are useful", "Changes that are harmful"],
                ),
                ("Humans", ["Major organs of the body"]),
                ("Animals", ["Live on land or in water"]),
            ],
        )

    def test_module_lesson_outline_ignores_course_info_and_nests_bullets(self):
        text = """
        SAMPLE COURSE OUTLINE
        Course Information
        Course Title: Teaching Science
        Credit Units: 3 Units
        Weekly Course Outline
        Module
        Subtopics / Lessons
        Module 1: Matter and Its Properties
        Lesson 1: Introduction to Matter
        Lesson 2: States of Matter
        • Solids
        • Liquids
        • Gases
        Lesson 3: Physical Properties of Matter
        Module 2: Force, Motion, and
        Energy
        Lesson 1: Force and Its Effects
        Lesson 2: Forms of Energy
        • Heat
        • Light
        """

        nodes = parse_outline_text(text)

        self.assertEqual(
            [(node.title, [child.title for child in node.children]) for node in nodes],
            [
                (
                    "Matter and Its Properties",
                    ["Introduction to Matter", "States of Matter", "Physical Properties of Matter"],
                ),
                (
                    "Force, Motion, and Energy",
                    ["Force and Its Effects", "Forms of Energy"],
                ),
            ],
        )
        self.assertEqual(
            [child.title for child in nodes[0].children[1].children],
            ["Solids", "Liquids", "Gases"],
        )
        self.assertEqual(
            [child.title for child in nodes[1].children[1].children],
            ["Heat", "Light"],
        )

    def test_flat_module_with_numbered_topics_is_nested_without_hardcoded_lesson_labels(self):
        text = """
        Module 1: Matter and Materials
        1. Solid, Liquid and Gas
        2. Grouping Materials Based on Properties
        Module 2: Living Things
        1. Plants
        2. Animals
        """

        nodes = parse_outline_text(text)

        self.assertEqual(
            [(node.title, [child.title for child in node.children]) for node in nodes],
            [
                ("Matter and Materials", ["Solid, Liquid and Gas", "Grouping Materials Based on Properties"]),
                ("Living Things", ["Plants", "Animals"]),
            ],
        )

    def test_mixed_module_formats_are_kept_together(self):
        text = """
        GRADE 9- LIFE SCIENCE
        Module 1: Properties of Matter
        -Solid
        -Liquid
        -gas
        Module 2: Changes that undergo
        Lesson 1: Changes that undergo 1
        Lesson 2: Changes that undergo 2
        Module 3: Earth
        1. Crust
        2. Skin
        3. Air
        Module 4: Heaven
        • Angel
        • The zombie Apocalypse
        """

        nodes = parse_outline_text(text)

        self.assertEqual(
            [node.title for node in nodes],
            [
                "Properties of Matter",
                "Changes that undergo",
                "Earth",
                "Heaven",
            ],
        )
        self.assertEqual([child.title for child in nodes[0].children], ["Solid", "Liquid", "gas"])
        self.assertEqual([child.title for child in nodes[1].children], ["Changes that undergo 1", "Changes that undergo 2"])
        self.assertEqual([child.title for child in nodes[2].children], ["Crust", "Skin", "Air"])
        self.assertEqual([child.title for child in nodes[3].children], ["Angel", "The zombie Apocalypse"])

    def test_wrapped_lesson_title_merges_generic_continuations(self):
        text = """
        Weekly Course Outline
        Module 1: Inquiry Systems
        Lesson 1:
        Pattern Analysis
        Across Environments:
        Field Applications
        Compare field observations with recorded data.
        Lesson 2:
        Evidence Mapping
        and Interpretation
        """

        nodes = parse_outline_text(text)

        self.assertEqual(nodes[0].title, "Inquiry Systems")
        self.assertEqual(
            [child.title for child in nodes[0].children],
            [
                "Pattern Analysis Across Environments: Field Applications",
                "Evidence Mapping and Interpretation",
            ],
        )

    def test_lesson_titles_under_same_module_remain_siblings(self):
        text = """
        Module 1: Integrated Systems
        Lesson 1: Structure Mapping
        Lesson 2: Signal Pathways
        Lesson 3: Feedback Processes
        Lesson 4: System Maintenance
        """

        nodes = parse_outline_text(text)

        self.assertEqual(nodes[0].title, "Integrated Systems")
        self.assertEqual(
            [child.title for child in nodes[0].children],
            ["Structure Mapping", "Signal Pathways", "Feedback Processes", "System Maintenance"],
        )
        self.assertTrue(all(child.children == [] for child in nodes[0].children))

    def test_outline_candidates_merge_wrapped_lesson_titles_before_filtering(self):
        text = """
        Module 1: Dynamic Processes
        Lesson 1:
        Energy Transfer
        Across Systems:
        Applied Investigation
        Learning Focus
        Analyze diagrams and observations.
        """

        candidates = _build_outline_candidates(text)
        raw_lines = [candidate["raw"] for candidate in candidates]

        self.assertIn(
            "Lesson 1: Energy Transfer Across Systems: Applied Investigation",
            raw_lines,
        )
        self.assertNotIn("Energy Transfer", raw_lines)
        self.assertNotIn("Across Systems:", raw_lines)

    def test_outline_candidates_repair_dangling_plain_titles_before_filtering(self):
        text = """
        Module 1: Life Processes
        Reproduction Among
        Living Systems
        Reproduction in Non
        Seed Organisms
        Classroom Orientation (Setting of
        Classroom Rules)
        """

        candidates = _build_outline_candidates(text)
        raw_lines = [candidate["raw"] for candidate in candidates]
        titles = [candidate["title"] for candidate in candidates]

        self.assertIn("Reproduction Among Living Systems", raw_lines)
        self.assertIn("Reproduction in Non Seed Organisms", raw_lines)
        self.assertNotIn("Reproduction Among", raw_lines)
        self.assertNotIn("Reproduction in Non", raw_lines)
        self.assertNotIn("Classroom Orientation (Setting of Classroom Rules)", titles)

    def test_table_outline_candidates_use_topic_column_when_header_exists(self):
        class FakeTable:
            def extract(self):
                return [
                    ["Week", "Topics / Content", "Learning Focus", "Activities / Output"],
                    ["1", "Module 1: Dynamic Processes\nLesson 1:\nPattern Flow\nAnalysis", "Explain relationships.", "Concept map"],
                    ["2", "Lesson 2:\nComparative Models", "Describe model limits.", "Small group report"],
                ]

        class FakeTables:
            tables = [FakeTable()]

        class FakePage:
            number = 0

            def find_tables(self):
                return FakeTables()

        text = _extract_pdf_table_outline_text([FakePage()])

        self.assertIn("Lesson 1: Pattern Flow Analysis", text)
        self.assertIn("Lesson 2: Comparative Models", text)
        self.assertNotIn("\nExplain relationships.", text)
        self.assertNotIn("\nConcept map", text)

    @patch("lessons.services.outline_parser._transcribe_image_only_outline_pdf", return_value="Module 1: Matter")
    @patch("lessons.services.outline_parser._extract_pdf_table_outline_text", return_value="")
    def test_image_only_outline_pdf_uses_vision_transcription(self, _mock_table, mock_transcribe):
        import os
        import tempfile
        import fitz

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            path = handle.name
        try:
            document = fitz.open()
            document.new_page(width=200, height=200)
            document.save(path)
            document.close()

            text = extract_outline_text(path, "pdf")

            self.assertIn("VISION PAGE TRANSCRIPTION", text)
            self.assertIn("Module 1: Matter", text)
            mock_transcribe.assert_called_once()
        finally:
            os.unlink(path)


class OutlineTitleFragmentTests(TestCase):
    def test_single_word_outline_fragment_is_not_a_final_title(self):
        self.assertFalse(_looks_like_plain_outline_title_start("Common"))
        self.assertFalse(_looks_like_plain_outline_title_start("Reproductive"))
        self.assertFalse(_looks_like_plain_outline_title_start("Biodiversity"))

    def test_structural_single_word_title_after_lesson_prefix_is_allowed(self):
        self.assertTrue(_looks_like_plain_outline_title_start("Lesson 1: Reproductive"))

    def test_two_word_dangling_fragment_is_not_a_final_title(self):
        self.assertFalse(_looks_like_plain_outline_title_start("Changes that"))


class LearningObjectPreservationTests(TestCase):
    def test_overview_scaffolding_and_topic_catalog_are_not_learning_objects(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "UNIT OVERVIEW", "line_count": 1, "is_bold": True},
            {
                "block_id": 2,
                "page": 1,
                "text": "Matter has observable properties that can change under different conditions.",
                "line_count": 1,
            },
            {
                "block_id": 3,
                "page": 1,
                "text": "Several reading resources are provided at different reading levels.",
                "line_count": 1,
            },
            {"block_id": 4, "page": 1, "text": "THE BIG IDEA", "line_count": 1, "is_bold": True},
            {
                "block_id": 5,
                "page": 1,
                "text": "Materials can change when they are heated or cooled.",
                "line_count": 1,
            },
            {"block_id": 6, "page": 1, "text": "Other topics", "line_count": 1, "is_bold": True},
            {
                "block_id": 7,
                "page": 1,
                "text": "This unit also addresses several related topics.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual([item["title"] for item in learning_objects], ["THE BIG IDEA"])
        self.assertEqual(
            learning_objects[0]["content"],
            "Materials can change when they are heated or cooled.",
        )

    @patch("lessons.services.content_generator.fitz.open")
    def test_small_uncaptioned_page_decoration_is_not_an_image_object(self, mock_open):
        import fitz

        class FakePixmap:
            def tobytes(self, _extension):
                return b"png"

        class FakePage:
            rect = fitz.Rect(0, 0, 612, 792)

            def get_text(self, _format):
                return {
                    "blocks": [
                        {
                            "type": 1,
                            "bbox": (20, 100, 120, 240),
                            "image": b"small-decoration",
                        },
                        {
                            "type": 1,
                            "bbox": (180, 250, 430, 450),
                            "image": b"large-visual",
                        },
                    ]
                }

            def get_pixmap(self, **_kwargs):
                return FakePixmap()

        class FakeDocument:
            def __iter__(self):
                return iter([FakePage()])

            def close(self):
                pass

        mock_open.return_value = FakeDocument()

        images = extract_meaningful_pdf_images("unused.pdf")

        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["width"], 250)
        self.assertEqual(images[0]["height"], 200)

    def test_numbered_concept_keeps_its_subheadings_and_short_bullet_examples(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "1. Solid", "line_count": 1, "is_bold": True},
            {
                "block_id": 2,
                "page": 1,
                "text": "A solid is a state of matter with a definite shape and volume.",
                "line_count": 1,
            },
            {"block_id": 3, "page": 1, "text": "Particles in a Solid", "line_count": 1, "is_bold": True},
            {
                "block_id": 4,
                "page": 1,
                "text": "Particles in a solid are packed closely and remain in fixed positions.",
                "line_count": 1,
            },
            {"block_id": 5, "page": 1, "text": "Examples of Solids", "line_count": 1, "is_bold": True},
            {"block_id": 6, "page": 1, "text": "•\nRock", "line_count": 2},
            {"block_id": 7, "page": 1, "text": "2. Liquid", "line_count": 1, "is_bold": True},
            {
                "block_id": 8,
                "page": 1,
                "text": "A liquid is matter with a definite volume but no definite shape.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual([item["title"] for item in learning_objects], ["Solid", "Liquid"])
        self.assertIn("Particles in a Solid:", learning_objects[0]["content"])
        self.assertIn("Examples of Solids:", learning_objects[0]["content"])
        self.assertIn("Rock", learning_objects[0]["content"])

    def test_enumerated_list_after_colon_stays_with_its_introductory_idea(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Introduction", "line_count": 1, "is_bold": True},
            {
                "block_id": 2,
                "page": 1,
                "text": "Two features are important when comparing the states:",
                "line_count": 1,
            },
            {"block_id": 3, "page": 1, "text": "1. Their shape and volume", "line_count": 1, "is_bold": True},
            {"block_id": 4, "page": 1, "text": "2. How their particles move", "line_count": 1, "is_bold": True},
            {"block_id": 5, "page": 1, "text": "1. Solid", "line_count": 1, "is_bold": True},
            {
                "block_id": 6,
                "page": 1,
                "text": "A solid is matter with a definite shape and volume.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual([item["title"] for item in learning_objects], ["Introduction", "Solid"])
        self.assertIn("1. Their shape and volume", learning_objects[0]["content"])
        self.assertIn("2. How their particles move", learning_objects[0]["content"])

    def test_inline_example_keeps_following_transition_and_result(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Example 2: Water in a Freezer", "line_count": 1},
            {"block_id": 2, "page": 1, "text": "Water is initially a liquid.", "line_count": 1},
            {"block_id": 3, "page": 1, "text": "Eventually:", "line_count": 1},
            {"block_id": 4, "page": 1, "text": "Water → Ice", "line_count": 1},
            {"block_id": 5, "page": 1, "text": "The liquid becomes a solid.", "line_count": 1},
            {"block_id": 6, "page": 1, "text": "Example 3: Wet Clothes", "line_count": 1},
            {"block_id": 7, "page": 1, "text": "Wet clothes contain liquid water.", "line_count": 1},
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(len(learning_objects), 2)
        self.assertEqual(learning_objects[0]["title"], "Example 2")
        self.assertIn("Water is initially a liquid.", learning_objects[0]["content"])
        self.assertIn("Eventually:", learning_objects[0]["content"])
        self.assertIn("The liquid becomes a solid.", learning_objects[0]["content"])
        self.assertEqual(learning_objects[1]["title"], "Example 3: Wet Clothes")

    def test_relationship_explanation_and_inline_example_stay_under_the_concept(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Deposition", "line_count": 1},
            {"block_id": 2, "page": 1, "text": "Gas → Solid", "line_count": 1},
            {
                "block_id": 3,
                "page": 1,
                "text": "Deposition changes a gas directly into a solid.",
                "line_count": 1,
            },
            {
                "block_id": 4,
                "page": 1,
                "text": "Example:\nFrost forms when water vapor changes directly into ice.",
                "line_count": 2,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(len(learning_objects), 1)
        self.assertEqual(learning_objects[0]["title"], "Deposition")
        self.assertIn("Gas → Solid", learning_objects[0]["content"])
        self.assertIn("Example:", learning_objects[0]["content"])
        self.assertIn("Frost forms", learning_objects[0]["content"])

    def test_rhetorical_question_is_removed_but_its_explanation_is_retained(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Liquid", "line_count": 1},
            {
                "block_id": 2,
                "page": 1,
                "text": "A liquid is matter with a definite volume but no definite shape.",
                "line_count": 1,
            },
            {"block_id": 3, "page": 1, "text": "What happens to the particles?", "line_count": 1},
            {
                "block_id": 4,
                "page": 1,
                "text": "The particles remain close together but can move past one another.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(len(learning_objects), 1)
        self.assertNotIn("?", learning_objects[0]["content"])
        self.assertIn("remain close together", learning_objects[0]["content"])

    def test_accessibility_review_removes_editorial_text_without_losing_relation(self):
        reviewed = review_learning_objects_for_bvi_learners(
            [
                {
                    "type": "lesson_content",
                    "title": "Deposition",
                    "content": "gas → solid\nmore like a teacher notes not a random info",
                },
                {
                    "type": "lesson_content",
                    "title": "Yes",
                    "content": (
                        "You want it to read like actual teacher-prepared lesson notes, "
                        "with examples and questions rather than a collection of facts."
                    ),
                },
            ]
        )

        self.assertEqual(len(reviewed), 1)
        self.assertEqual(reviewed[0]["title"], "Deposition")
        self.assertEqual(reviewed[0]["content"], "gas → solid")

    def test_narration_verbalizes_visual_relationship_arrow(self):
        narration = build_narration_script_from_learning_objects(
            [
                {
                    "type": "lesson_content",
                    "title": "Deposition",
                    "content": "gas → solid",
                }
            ]
        )

        self.assertEqual(narration[0]["content"], "Deposition. gas to solid.")

    def test_short_glossary_definition_is_preserved_and_fully_narrated(self):
        learning_object = {
            "type": "lesson_content",
            "title": "shape",
            "content": "how something looks around the outside",
            "source_excerpt": "shape\nhow something looks around the outside",
        }

        narration = build_narration_script_from_learning_objects([learning_object])

        self.assertEqual(
            learning_object["content"],
            "how something looks around the outside",
        )
        self.assertEqual(
            narration[0]["content"],
            "shape. how something looks around the outside.",
        )

    def test_borderless_aligned_rows_are_detected_as_one_table(self):
        import fitz

        def row(y, cells):
            lines = []
            for x, value in cells:
                lines.append(
                    {
                        "bbox": (x, y, x + 55, y + 12),
                        "spans": [{"text": value, "bbox": (x, y, x + 55, y + 12)}],
                    }
                )
            return {
                "type": 0,
                "bbox": (cells[0][0], y, cells[-1][0] + 55, y + 12),
                "lines": lines,
            }

        page_dict = {
            "blocks": [
                row(80, [(70, "Property"), (180, "A"), (300, "B")]),
                row(108, [(70, "Shape"), (180, "First"), (300, "Second")]),
                row(136, [(70, "Volume"), (180, "Third"), (300, "Fourth")]),
            ]
        }

        regions = _borderless_table_regions(page_dict, fitz.Rect(0, 0, 600, 800))

        self.assertEqual(len(regions), 1)
        self.assertEqual(len(regions[0]["rows"]), 3)

    def test_nearly_full_page_text_alignment_is_not_accepted_as_a_table(self):
        import fitz

        page = fitz.Rect(0, 0, 600, 800)
        pseudo_table = fitz.Rect(60, 50, 540, 760)

        self.assertFalse(_plausible_table_bbox(pseudo_table, page, "text"))
        self.assertFalse(_plausible_table_bbox(pseudo_table, page, "borderless"))
        self.assertTrue(_plausible_table_bbox(pseudo_table, page, "lines"))

    def test_repeated_instruction_on_another_page_is_not_an_extraction_duplicate(self):
        blocks = [
            {
                "block_id": 1,
                "page": 1,
                "text": "A solid is a state of matter with definite shape and volume.",
                "bbox": (50, 100, 400, 120),
            },
            {
                "block_id": 2,
                "page": 2,
                "text": "A solid is a state of matter with definite shape and volume.",
                "bbox": (50, 100, 400, 120),
            },
        ]

        classified = classify_instructional_blocks(blocks)

        self.assertEqual([item["category"] for item in classified], ["lesson_content", "lesson_content"])

    def test_overlapping_duplicate_text_layer_is_removed(self):
        blocks = [
            {
                "block_id": 1,
                "page": 1,
                "text": "A liquid is a state of matter with a definite volume.",
                "bbox": (50, 100, 300, 120),
            },
            {
                "block_id": 2,
                "page": 1,
                "text": "A liquid is a state of matter with a definite volume.",
                "bbox": (50, 100, 300, 120),
            },
        ]

        classified = classify_instructional_blocks(blocks)

        self.assertEqual(classified[0]["category"], "lesson_content")
        self.assertEqual(classified[1]["category"], "decorative_or_noise")

    def test_definition_explanation_and_example_stay_in_one_concept_object(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Solid", "line_count": 1},
            {"block_id": 2, "page": 1, "text": "Definition", "line_count": 1},
            {
                "block_id": 3,
                "page": 1,
                "text": "A solid has a definite shape and a definite volume.",
                "line_count": 1,
            },
            {"block_id": 4, "page": 1, "text": "Explanation", "line_count": 1},
            {
                "block_id": 5,
                "page": 1,
                "text": "Its particles remain closely arranged and vibrate in place.",
                "line_count": 1,
            },
            {"block_id": 6, "page": 1, "text": "Examples", "line_count": 1},
            {
                "block_id": 7,
                "page": 1,
                "text": "A rock and an ice cube are examples of solids.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(len(learning_objects), 1)
        self.assertEqual(learning_objects[0]["title"], "Solid")
        self.assertIn("Definition:", learning_objects[0]["content"])
        self.assertIn("Explanation:", learning_objects[0]["content"])
        self.assertIn("Examples:", learning_objects[0]["content"])
        self.assertIn("A rock and an ice cube", learning_objects[0]["content"])

    def test_direct_concept_definition_and_following_examples_stay_together(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Solid", "line_count": 1},
            {
                "block_id": 2,
                "page": 1,
                "text": "A solid is a state of matter with a definite shape and volume.",
                "line_count": 1,
            },
            {"block_id": 3, "page": 1, "text": "Examples", "line_count": 1},
            {
                "block_id": 4,
                "page": 1,
                "text": "A rock, pencil, and ice cube are examples of solids.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(len(learning_objects), 1)
        self.assertEqual(learning_objects[0]["title"], "Solid")
        self.assertIn("Examples:", learning_objects[0]["content"])

    def test_standalone_example_heading_without_active_concept_is_preserved(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Examples", "line_count": 1},
            {
                "block_id": 2,
                "page": 1,
                "text": "Several materials can be observed and compared by their properties.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(len(learning_objects), 1)
        self.assertEqual(learning_objects[0]["title"], "Examples")

    def test_instructional_table_is_one_image_object_with_blank_teacher_description(self):
        rows = [
            ["Property", "First category", "Second category"],
            ["Shape", "Description one", "Description two"],
            ["Volume", "Description three", "Description four"],
        ]
        image = {
            "page_number": 2,
            "index": 0,
            "width": 500,
            "height": 220,
            "extension": "png",
            "image_url": "/media/extracted_images/table.png",
            "title": "Category Comparison",
            "visible_text": "\n".join(" | ".join(row) for row in rows),
            "is_table": True,
        }

        self.assertTrue(_instructional_table_rows(rows))
        descriptions = describe_pdf_images([image])
        learning_objects = build_section_learning_objects([], descriptions)

        self.assertEqual(len(learning_objects), 1)
        self.assertEqual(learning_objects[0]["type"], "image_description")
        self.assertEqual(learning_objects[0]["title"], "Category Comparison")
        self.assertEqual(learning_objects[0]["content"], "")
        self.assertTrue(learning_objects[0]["is_table"])

    def test_table_image_is_ordered_at_its_pdf_position(self):
        blocks = [
            {
                "block_id": 1,
                "page": 1,
                "text": "Matter",
                "line_count": 1,
                "is_bold": True,
                "bbox": (60, 50, 180, 65),
            },
            {
                "block_id": 2,
                "page": 1,
                "text": "Matter is anything that has mass and occupies space.",
                "line_count": 1,
                "bbox": (60, 80, 450, 95),
            },
            {
                "block_id": 3,
                "page": 2,
                "text": "State Changes",
                "line_count": 1,
                "is_bold": True,
                "bbox": (60, 300, 220, 315),
            },
            {
                "block_id": 4,
                "page": 2,
                "text": "Heating and cooling can change the state of matter.",
                "line_count": 1,
                "bbox": (60, 330, 460, 345),
            },
        ]
        image = {
            "page_number": 2,
            "index": 0,
            "width": 400,
            "height": 150,
            "extension": "png",
            "image_url": "/media/extracted_images/table.png",
            "title": "State Comparison",
            "visible_text": "Property | A | B",
            "bbox": (60, 100, 460, 250),
            "is_table": True,
        }

        classified = classify_instructional_blocks(blocks)
        learning_objects = build_section_learning_objects(classified, describe_pdf_images([image]))

        self.assertEqual(
            [item["title"] for item in learning_objects],
            ["Matter", "State Comparison", "State Changes"],
        )

    def test_table_text_blocks_are_removed_by_page_and_layout_region(self):
        blocks = [
            {"block_id": 1, "page": 2, "text": "Table cell", "bbox": (100, 100, 180, 130)},
            {"block_id": 2, "page": 2, "text": "Paragraph below", "bbox": (100, 300, 250, 330)},
            {"block_id": 3, "page": 3, "text": "Same coordinates next page", "bbox": (100, 100, 240, 130)},
        ]
        tables = [{"page_number": 2, "bbox": (50, 50, 500, 250)}]

        kept = exclude_text_blocks_inside_tables(blocks, tables)

        self.assertEqual([block["block_id"] for block in kept], [2, 3])

    def test_sparse_page_alignment_is_not_treated_as_a_table(self):
        rows = [
            ["Heading", "", "", ""],
            ["Paragraph", "", "", "Page"],
            ["", "", "", ""],
        ]

        self.assertFalse(_instructional_table_rows(rows))

    def test_fill_in_blank_activity_columns_are_not_treated_as_a_table(self):
        rows = [
            ["First search suggestion", "Second search suggestion"],
            ["Third search suggestion", "Fourth search suggestion"],
            ["melting point of ______", "boiling point of ______"],
        ]

        self.assertFalse(_instructional_table_rows(rows))

    def test_short_state_relationships_merge_within_their_shared_section(self):
        objects = [
            {
                "type": "lesson_content",
                "section_title": "State Changes",
                "title": title,
                "content": content,
                "source_page": 2,
                "source_block_id": index,
            }
            for index, (title, content) in enumerate(
                [
                    ("Melting", "solid → liquid"),
                    ("Freezing", "liquid → solid"),
                    ("Evaporation", "liquid → gas"),
                    ("Condensation", "gas → liquid"),
                ],
                start=1,
            )
        ]

        balanced = balance_learning_object_chunks(objects)

        self.assertEqual(len(balanced), 1)
        self.assertEqual(balanced[0]["title"], "State Changes")
        self.assertEqual(balanced[0]["section_title"], "")
        self.assertIn("Melting: solid → liquid", balanced[0]["content"])
        self.assertIn("Condensation: gas → liquid", balanced[0]["content"])
        self.assertEqual(balanced[0]["source_block_ids"], [1, 2, 3, 4])

    @patch.dict("os.environ", {"LEARNING_OBJECT_MAX_WORDS": "20"})
    def test_long_learning_object_splits_only_at_authored_boundaries(self):
        content = "\n".join(
            [
                "Matter has mass and occupies space.",
                "A solid has a definite shape and volume.",
                "A liquid has definite volume but no definite shape.",
                "A gas has no definite shape and no definite volume.",
                "Gas particles are far apart and move freely.",
            ]
        )
        objects = [
            {
                "type": "lesson_content",
                "section_title": "",
                "title": "Key Facts",
                "content": content,
                "source_page": 3,
                "source_block_id": 9,
            }
        ]

        balanced = balance_learning_object_chunks(objects)

        self.assertGreater(len(balanced), 1)
        self.assertTrue(all("Part" in item["title"] for item in balanced))
        self.assertEqual(
            "\n".join(item["content"] for item in balanced),
            content,
        )

    @patch.dict("os.environ", {"LEARNING_OBJECT_MAX_WORDS": "14"})
    def test_visual_pdf_line_wrap_never_becomes_a_chunk_boundary(self):
        content = (
            "The material takes the shape of the container\n"
            "that holds it.\n"
            "For example, the same material may be poured into\n"
            "a bowl, where it changes shape to fit the bowl.\n"
            "Its amount remains unchanged during this observation."
        )
        objects = [
            {
                "type": "lesson_content",
                "section_title": "",
                "title": "Example",
                "content": content,
            }
        ]

        balanced = balance_learning_object_chunks(objects)
        chunks = [item["content"] for item in balanced]

        self.assertGreater(len(chunks), 1)
        self.assertIn("container that holds it.", "\n".join(chunks))
        self.assertIn("poured into a bowl,", "\n".join(chunks))
        self.assertFalse(any(chunk.rstrip().endswith("container") for chunk in chunks))
        self.assertFalse(any(chunk.rstrip().endswith("into") for chunk in chunks))
        self.assertFalse(any(chunk.lstrip().startswith("that holds it") for chunk in chunks))
        self.assertFalse(any(chunk.lstrip().startswith("a bowl") for chunk in chunks))

    @patch.dict("os.environ", {"LEARNING_OBJECT_MAX_WORDS": "12"})
    def test_short_authored_list_lines_remain_separate_when_chunked(self):
        content = (
            "Particle properties:\n"
            "Very close together\n"
            "Tightly packed\n"
            "Able to vibrate in place\n"
            "These properties explain why the material keeps its shape."
        )
        balanced = balance_learning_object_chunks(
            [{"type": "lesson_content", "title": "Particles", "content": content}]
        )
        reconstructed = "\n".join(item["content"] for item in balanced)

        self.assertIn("Very close together\nTightly packed", reconstructed)
        self.assertNotIn("Very close together Tightly packed", reconstructed)

    @patch.dict("os.environ", {"LEARNING_OBJECT_MAX_WORDS": "8"})
    def test_numbered_list_marker_stays_with_its_text_when_chunked(self):
        content = "The common forms are:\n1. First form\n2. Second form\n3. Third form"
        balanced = balance_learning_object_chunks(
            [{"type": "lesson_content", "title": "Forms", "content": content}]
        )
        reconstructed = "\n".join(item["content"] for item in balanced)

        self.assertIn("1. First form", reconstructed)
        self.assertIn("2. Second form", reconstructed)
        self.assertNotIn("1.\nFirst form", reconstructed)

    def test_unrelated_short_definitions_are_not_merged(self):
        objects = [
            {
                "type": "lesson_content",
                "section_title": "Vocabulary",
                "title": "Mass",
                "content": "Amount of matter in an object.",
            },
            {
                "type": "lesson_content",
                "section_title": "Vocabulary",
                "title": "Texture",
                "content": "How a surface feels when touched.",
            },
        ]

        balanced = balance_learning_object_chunks(objects)

        self.assertEqual([item["title"] for item in balanced], ["Mass", "Texture"])

    def test_authoring_comment_does_not_become_a_learning_object(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Deposition", "line_count": 1},
            {"block_id": 2, "page": 1, "text": "gas → solid", "line_count": 1},
            {"block_id": 3, "page": 1, "text": "Yes", "line_count": 1},
            {
                "block_id": 4,
                "page": 1,
                "text": (
                    "You want it to read like teacher-prepared lesson notes, where ideas are "
                    "explained in sequence with examples and questions rather than a collection of facts."
                ),
                "line_count": 2,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])
        all_text = "\n".join(
            f"{item.get('title', '')}\n{item.get('content', '')}" for item in learning_objects
        )

        self.assertNotIn("teacher-prepared lesson notes", all_text)
        self.assertNotIn("\nYes\n", f"\n{all_text}\n")

    def test_pdf_line_breaks_are_preserved_during_block_cleanup(self):
        cleaned = clean_block_text(
            "water vapor\n"
            "the state of water in which it is an invisible gas\n"
            "A. liquid\n"
            "B. gas"
        )

        self.assertEqual(
            cleaned,
            "water vapor\n"
            "the state of water in which it is an invisible gas\n"
            "A. liquid\n"
            "B. gas",
        )

    def test_fill_in_blank_and_answer_choices_do_not_become_learning_objects(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Matter", "line_count": 1},
            {
                "block_id": 2,
                "page": 1,
                "text": "Matter is anything that has mass and occupies space.",
                "line_count": 1,
            },
            {
                "block_id": 3,
                "page": 2,
                "text": "Directions: Choose the correct answer.",
                "line_count": 1,
            },
            {
                "block_id": 4,
                "page": 2,
                "text": "13. _____ water vapor",
                "line_count": 1,
            },
            {
                "block_id": 5,
                "page": 2,
                "text": "A. liquid B. gas C. solid D. mixture",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])
        all_text = "\n".join(
            f"{item['title']}\n{item['content']}" for item in learning_objects
        )

        self.assertEqual([item["title"] for item in learning_objects], ["Matter"])
        self.assertNotIn("water vapor", all_text)
        self.assertNotIn("A. liquid", all_text)

    def test_qa_blocks_are_assessments_not_learning_objects(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Matter", "line_count": 1},
            {
                "block_id": 2,
                "page": 1,
                "text": "Matter is anything that has mass and occupies space.",
                "line_count": 1,
            },
            {
                "block_id": 3,
                "page": 1,
                "text": "Q: Does water need to be hot to evaporate?",
                "line_count": 1,
            },
            {
                "block_id": 4,
                "page": 1,
                "text": "A: Water can evaporate at cooler temperatures.",
                "line_count": 1,
            },
        ]

        classified = classify_instructional_blocks(blocks)
        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual([item["category"] for item in classified[-2:]], ["assessment", "assessment"])
        self.assertEqual([item["title"] for item in learning_objects], ["Matter"])

    def test_question_only_document_is_detected_as_assessment(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Science Review Questions", "line_count": 1},
            {"block_id": 2, "page": 1, "text": "1. Define matter.", "line_count": 1},
            {"block_id": 3, "page": 1, "text": "2. Explain why a solid keeps its shape.", "line_count": 1},
            {"block_id": 4, "page": 1, "text": "3. Compare a liquid and a gas.", "line_count": 1},
            {"block_id": 5, "page": 1, "text": "4. Which state has a definite volume?", "line_count": 1},
        ]

        classified = classify_instructional_blocks(blocks)

        self.assertEqual(detect_instructional_document_role(classified), "assessment")
        self.assertEqual(
            [item["category"] for item in classified[1:]],
            ["assessment", "assessment", "assessment", "assessment"],
        )

    def test_true_false_and_multiple_choice_document_is_detected_as_assessment(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "A. True or False", "line_count": 1},
            {"block_id": 2, "page": 1, "text": "1. A solid has a definite shape and volume.", "line_count": 1},
            {"block_id": 3, "page": 1, "text": "True / False", "line_count": 1},
            {"block_id": 4, "page": 1, "text": "2. A liquid takes the shape of its container.", "line_count": 1},
            {"block_id": 5, "page": 1, "text": "True / False", "line_count": 1},
            {"block_id": 6, "page": 2, "text": "B. Multiple Choice", "line_count": 1},
            {"block_id": 7, "page": 2, "text": "1. Which state of matter has a definite shape?", "line_count": 1},
            {"block_id": 8, "page": 2, "text": "A. Solid B. Liquid C. Gas D. Plasma", "line_count": 1},
            {"block_id": 9, "page": 2, "text": "2. Which state fills its entire container?", "line_count": 1},
            {"block_id": 10, "page": 2, "text": "A. Solid B. Liquid C. Gas D. Ice", "line_count": 1},
        ]

        classified = classify_instructional_blocks(blocks)

        self.assertEqual(detect_instructional_document_role(classified), "assessment")

    def test_numbered_instruction_questions_are_extracted_as_separate_prompts(self):
        payloads = detected_question_payloads(
            [
                {
                    "block_id": 1,
                    "page": 1,
                    "category": "assessment",
                    "text": "1. Define matter.\n2. Explain why a solid keeps its shape.\n3. Compare liquids and gases.",
                }
            ]
        )

        self.assertEqual(
            [payload["prompt"] for payload in payloads],
            [
                "1. Define matter.",
                "2. Explain why a solid keeps its shape.",
                "3. Compare liquids and gases.",
            ],
        )

    def test_true_false_statements_are_extracted_from_question_only_document(self):
        classified = [
            {"block_id": 1, "page": 1, "category": "assessment", "text": "A. True or False"},
            {
                "block_id": 2,
                "page": 1,
                "category": "lesson_content",
                "text": "1. A solid has a definite shape and definite volume.",
            },
            {"block_id": 3, "page": 1, "category": "lesson_content", "text": "True / False"},
            {
                "block_id": 4,
                "page": 1,
                "category": "lesson_content",
                "text": "2. A liquid takes the shape of its container.",
            },
            {"block_id": 5, "page": 1, "category": "lesson_content", "text": "True / False"},
            {"block_id": 6, "page": 1, "category": "assessment", "text": "B. Multiple Choice"},
            {
                "block_id": 7,
                "page": 1,
                "category": "assessment",
                "text": "1. Which example is a gas?",
            },
            {
                "block_id": 8,
                "page": 1,
                "category": "assessment",
                "text": "A. Milk\nB. Rock\nC. Water\nD. Oxygen",
            },
        ]

        payloads = detected_question_payloads(classified)

        self.assertEqual(
            [item["prompt"] for item in payloads],
            [
                "1. A solid has a definite shape and definite volume.",
                "2. A liquid takes the shape of its container.",
                "1. Which example is a gas?",
            ],
        )

    def test_teacher_check_prompt_and_expected_answer_are_not_learning_objects(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Solid", "line_count": 1},
            {
                "block_id": 2,
                "page": 1,
                "text": "A solid keeps its own shape because its particles remain closely arranged.",
                "line_count": 1,
            },
            {"block_id": 3, "page": 1, "text": "Teacher Check", "line_count": 1},
            {"block_id": 4, "page": 1, "text": "Ask", "line_count": 1},
            {
                "block_id": 5,
                "page": 1,
                "text": "If an object is placed inside a container, does it change its shape?",
                "line_count": 1,
            },
            {"block_id": 6, "page": 1, "text": "Expected answer", "line_count": 1},
            {
                "block_id": 7,
                "page": 1,
                "text": "No. The object keeps its own shape.",
                "line_count": 1,
            },
            {"block_id": 8, "page": 1, "text": "Liquid", "line_count": 1},
            {
                "block_id": 9,
                "page": 1,
                "text": "A liquid has a definite volume and takes the shape of its container.",
                "line_count": 1,
            },
        ]

        classified = classify_instructional_blocks(blocks)
        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])
        all_text = "\n".join(
            f"{item['title']}\n{item['content']}" for item in learning_objects
        )

        self.assertEqual(classified[2]["category"], "assessment")
        self.assertEqual(classified[3]["category"], "assessment")
        self.assertEqual(classified[4]["category"], "assessment")
        self.assertEqual(classified[5]["category"], "answer_key")
        self.assertEqual([item["title"] for item in learning_objects], ["Solid", "Liquid"])
        self.assertNotIn("Teacher Check", all_text)
        self.assertNotIn("Expected answer", all_text)
        self.assertNotIn("does it change", all_text)

    def test_multiline_vocabulary_rows_become_separate_definition_objects(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Core Science Terms", "line_count": 1},
            {
                "block_id": 2,
                "page": 1,
                "text": "matter\nanything that takes up space and has weight",
                "line_count": 2,
            },
            {
                "block_id": 3,
                "page": 1,
                "text": "solid\nmatter that keeps its shape and size",
                "line_count": 2,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])
        by_title = {item["title"]: item["content"] for item in learning_objects}

        self.assertEqual(list(by_title), ["matter", "solid"])
        self.assertEqual(by_title["matter"], "anything that takes up space and has weight")
        self.assertEqual(by_title["solid"], "matter that keeps its shape and size")

    def test_private_use_bullet_lines_do_not_become_learning_object_titles(self):
        blocks = [
            {
                "block_id": 1,
                "page": 1,
                "text": "Comparing the Three States",
                "line_count": 1,
            },
            {
                "block_id": 2,
                "page": 1,
                "text": "\uf06c\nShape: Solids keep their shape; liquids and gases take the shape of their container.",
                "line_count": 2,
            },
            {
                "block_id": 3,
                "page": 1,
                "text": "\uf06c\nVolume: Solids and liquids have definite volume; gases expand to fill available",
                "line_count": 2,
            },
            {
                "block_id": 4,
                "page": 1,
                "text": "space.",
                "line_count": 1,
            },
            {
                "block_id": 5,
                "page": 1,
                "text": "\uf06c\nParticle arrangement: Solid particles are tightly packed, liquid particles are close\nbut mobile, and gas particles are widely spaced.",
                "line_count": 3,
            },
            {
                "block_id": 6,
                "page": 1,
                "text": "\uf06c\nFlow: Liquids and gases can flow, while solids normally do not.",
                "line_count": 2,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(
            [item["title"] for item in learning_objects],
            ["Shape", "Volume", "Particle arrangement", "Flow"],
        )
        self.assertEqual(
            {item["section_title"] for item in learning_objects},
            {"Comparing the Three States"},
        )
        self.assertNotIn("\uf06c", str(learning_objects))
        self.assertEqual(
            learning_objects[1]["content"],
            "Solids and liquids have definite volume; gases expand to fill available\nspace.",
        )

    def test_non_instructional_front_matter_does_not_create_learning_objects(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Table of Contents", "line_count": 1},
            {"block_id": 2, "page": 1, "text": "Matter.............5", "line_count": 1},
            {"block_id": 3, "page": 1, "text": "Copyright © 2026", "line_count": 1},
            {"block_id": 4, "page": 1, "text": "All rights reserved.", "line_count": 1},
            {"block_id": 5, "page": 1, "text": "ISBN: 978-1-4028-9462-6", "line_count": 1},
            {"block_id": 6, "page": 2, "text": "Matter", "line_count": 1, "is_bold": True, "font_size": 14},
            {
                "block_id": 7,
                "page": 2,
                "text": "Matter is anything that has mass and occupies space.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])
        all_text = "\n".join(
            f"{item['title']}\n{item['content']}" for item in learning_objects
        )

        self.assertEqual([item["title"] for item in learning_objects], ["Matter"])
        self.assertIn("Matter is anything", all_text)
        self.assertNotIn("Table of Contents", all_text)
        self.assertNotIn("Copyright", all_text)
        self.assertNotIn("ISBN", all_text)

    def test_questions_answer_keys_and_references_never_become_learning_objects(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Solids", "line_count": 1},
            {
                "block_id": 2,
                "page": 1,
                "text": "A solid has a definite shape and volume.",
                "line_count": 1,
            },
            {"block_id": 3, "page": 1, "text": "Review Questions", "line_count": 1},
            {"block_id": 4, "page": 1, "text": "1. What is a solid?", "line_count": 1},
            {"block_id": 5, "page": 1, "text": "Answer Key", "line_count": 1},
            {"block_id": 6, "page": 1, "text": "1. A solid has definite shape.", "line_count": 1},
            {"block_id": 7, "page": 2, "text": "References", "line_count": 1},
            {"block_id": 8, "page": 2, "text": "https://example.com/science", "line_count": 1},
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])
        all_text = "\n".join(item["content"] for item in learning_objects)

        self.assertEqual([item["title"] for item in learning_objects], ["Solids"])
        self.assertNotIn("What is a solid", all_text)
        self.assertNotIn("Answer Key", all_text)
        self.assertNotIn("example.com", all_text)

    def test_repeated_page_headers_and_footers_are_classified_as_metadata(self):
        blocks = []
        block_id = 1
        for page in (1, 2, 3):
            blocks.extend(
                [
                    {
                        "block_id": block_id,
                        "page": page,
                        "text": f"SCIENCE 3 — Page {page}",
                        "line_count": 1,
                        "bbox": (40, 15, 250, 30),
                        "page_height": 800,
                    },
                    {
                        "block_id": block_id + 1,
                        "page": page,
                        "text": "Department of Education",
                        "line_count": 1,
                        "bbox": (40, 775, 250, 792),
                        "page_height": 800,
                    },
                ]
            )
            block_id += 2

        classified = classify_instructional_blocks(blocks)

        self.assertTrue(classified)
        self.assertTrue(all(item["category"] == "document_metadata" for item in classified))

    def test_uncertain_standalone_text_requires_review_instead_of_becoming_object(self):
        blocks = [
            {
                "block_id": 1,
                "page": 1,
                "text": "Prepared especially for classroom distribution",
                "line_count": 1,
            }
        ]

        classified = classify_instructional_blocks(blocks)
        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(classified[0]["category"], "needs_review")
        self.assertEqual(learning_objects, [])

    def test_extract_pdf_text_blocks_accepts_integer_span_colors(self):
        class FakeSpan:
            def __init__(self, text, flags, font, size, color):
                self._payload = {
                    "text": text,
                    "flags": flags,
                    "font": font,
                    "size": size,
                    "color": color,
                }

            def get(self, key, default=None):
                return self._payload.get(key, default)

        class FakeLine:
            def __init__(self):
                self._payload = {
                    "spans": [FakeSpan("The Sun", 0, "Arial", 12.0, 16711680)],
                }

            def get(self, key, default=None):
                return self._payload.get(key, default)

        class FakePage:
            def get_text(self, _format):
                return {
                    "blocks": [
                        {
                            "type": 0,
                            "lines": [FakeLine()],
                        }
                    ]
                }

        class FakeDocument:
            def __iter__(self):
                return iter([FakePage()])

            def close(self):
                pass

        with patch("lessons.services.instructional_content_classifier.fitz.open", return_value=FakeDocument()):
            blocks = extract_pdf_text_blocks("unused.pdf")

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["text"], "The Sun")
        self.assertEqual(blocks[0]["text_color"], 16711680)

    def test_transcribed_page_text_becomes_learning_blocks(self):
        blocks = _text_blocks_from_transcription(
            "Page 1\nWhat is Matter?\nMatter has mass and occupies space."
        )

        self.assertEqual([block["text"] for block in blocks], ["What is Matter?", "Matter has mass and occupies space."])
        self.assertEqual(blocks[0]["source"], "deterministic_page_text_fallback")

    def test_image_learning_object_uses_pdf_caption_and_waits_for_teacher_description(self):
        learning_objects = build_section_learning_objects(
            [],
            [
                {
                    "index": 0,
                    "page_number": 1,
                    "description": "Diagram showing the water cycle.",
                    "visible_text": "Evaporation, Condensation, Precipitation",
                    "caption": "Figure 2. The water cycle.",
                }
            ],
        )

        self.assertEqual(learning_objects[0]["title"], "Diagram showing the water cycle")
        self.assertEqual(learning_objects[0]["content"], "Diagram showing the water cycle.")

        pending = build_section_learning_objects(
            [],
            [
                {
                    "index": 0,
                    "page_number": 1,
                    "description": "",
                    "visible_text": "Evaporation, Condensation, Precipitation",
                    "caption": "Figure 2. The water cycle.",
                }
            ],
        )[0]
        self.assertEqual(pending["title"], "The water cycle")
        self.assertEqual(pending["content"], "")
        self.assertEqual(pending["source_excerpt"], "Figure 2. The water cycle.")

    def test_unheaded_body_block_uses_content_as_title(self):
        blocks = [
            {
                "block_id": 1,
                "page": 1,
                "text": "Matter is anything that has mass and occupies space.",
                "line_count": 1,
            }
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(learning_objects[0]["title"], "Matter is anything that has mass and occupies space")
        self.assertNotIn("Learning Object", learning_objects[0]["title"])

    def test_standalone_module_label_is_not_learning_object(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Module 1", "line_count": 1},
            {"block_id": 2, "page": 1, "text": "Properties of Matter", "line_count": 1},
            {
                "block_id": 3,
                "page": 1,
                "text": "Matter is anything that has mass and occupies space.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        by_title = {item["title"]: item["content"] for item in learning_objects}
        self.assertNotIn("Module 1", by_title)
        self.assertEqual(
            by_title["Properties of Matter"],
            "Matter is anything that has mass and occupies space.",
        )

    def test_objective_is_excluded_and_pdf_fragments_remain_under_concept(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Module 1", "line_count": 1},
            {"block_id": 2, "page": 1, "text": "Properties of Matter", "line_count": 1},
            {
                "block_id": 3,
                "page": 1,
                "text": "Relate observable properties to the state of a material.",
                "line_count": 1,
            },
            {"block_id": 4, "page": 1, "text": "Solid", "line_count": 1},
            {
                "block_id": 5,
                "page": 1,
                "text": "A solid has a definite shape. Its particles are packed",
                "line_count": 1,
            },
            {
                "block_id": 6,
                "page": 1,
                "text": "closely together and mainly vibrate in fixed positions.",
                "line_count": 1,
            },
            {
                "block_id": 7,
                "page": 1,
                "text": "Examples include a stone, pencil, wooden block, and ice cube.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])
        by_title = {item["title"]: item["content"] for item in learning_objects}
        all_text = "\n".join([*by_title, *by_title.values()])

        self.assertEqual(list(by_title), ["Solid"])
        self.assertNotIn("Module 1", all_text)
        self.assertNotIn("Relate observable properties", all_text)
        self.assertEqual(
            by_title["Solid"],
            "A solid has a definite shape. Its particles are packed\n"
            "closely together and mainly vibrate in fixed positions.\n"
            "Examples include a stone, pencil, wooden block, and ice cube.",
        )

    def test_existing_heading_keeps_inline_pdf_text_verbatim(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Examples", "line_count": 1},
            {
                "block_id": 2,
                "page": 1,
                "text": "Solid: book, chair, and stone.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(learning_objects[0]["title"], "Examples")
        self.assertEqual(learning_objects[0]["content"], "Solid: book, chair, and stone.")

    def test_empty_heading_is_container_for_multiple_labeled_definitions(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Caring for the Sense Organs", "line_count": 1},
            {
                "block_id": 2,
                "page": 1,
                "text": "• Eyes: read in a well-lighted place and rest after long screen use.",
                "line_count": 1,
            },
            {
                "block_id": 3,
                "page": 1,
                "text": "• Ears: avoid very loud sounds and clean only the outer part.",
                "line_count": 1,
            },
            {
                "block_id": 4,
                "page": 1,
                "text": "• Nose: avoid dusty places and never put small objects inside the nostrils.",
                "line_count": 1,
            },
            {
                "block_id": 5,
                "page": 1,
                "text": "• Tongue: brush the teeth and tongue daily and avoid food that is too hot.",
                "line_count": 1,
            },
            {
                "block_id": 6,
                "page": 1,
                "text": "• Skin: bathe regularly and protect the skin from too much sun.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])
        by_title = {item["title"]: item["content"] for item in learning_objects}

        self.assertNotIn("Caring for the Sense Organs", by_title)
        self.assertEqual(list(by_title), ["Eyes", "Ears", "Nose", "Tongue", "Skin"])
        self.assertEqual(
            {item["section_title"] for item in learning_objects},
            {"Caring for the Sense Organs"},
        )
        self.assertEqual(
            by_title["Eyes"],
            "read in a well-lighted place and rest after long screen use.",
        )
        self.assertEqual(
            by_title["Skin"],
            "bathe regularly and protect the skin from too much sun.",
        )

        narration = build_narration_script_from_learning_objects(learning_objects)
        self.assertEqual(
            narration[0]["content"],
            "In Caring for the Sense Organs. Eyes: "
            "read in a well-lighted place and rest after long screen use.",
        )
        self.assertEqual(
            narration[1]["content"],
            "Ears: avoid very loud sounds and clean only the outer part.",
        )

    def test_explained_parent_and_labeled_children_share_section_context(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Sense Organs", "line_count": 1},
            {
                "block_id": 2,
                "page": 1,
                "text": "Sense organs collect information from the surroundings.",
                "line_count": 1,
            },
            {
                "block_id": 3,
                "page": 1,
                "text": "Eyes: The eyes detect light and allow us to see.",
                "line_count": 1,
            },
            {
                "block_id": 4,
                "page": 1,
                "text": "Ears: The ears detect sound and help maintain balance.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual([item["title"] for item in learning_objects], ["Sense Organs", "Eyes", "Ears"])
        self.assertEqual(
            [item["section_title"] for item in learning_objects],
            ["Sense Organs", "Sense Organs", "Sense Organs"],
        )
        narration = build_narration_script_from_learning_objects(learning_objects)
        self.assertEqual(
            narration[0]["content"],
            "Sense Organs. Sense organs collect information from the surroundings.",
        )
        self.assertEqual(
            narration[1]["content"],
            "Eyes: The eyes detect light and allow us to see.",
        )

    def test_captioned_vector_figure_is_an_image_not_text_learning_objects(self):
        import os
        import tempfile
        import fitz

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            path = handle.name
        try:
            document = fitz.open()
            page = document.new_page(width=612, height=792)
            page.insert_text((180, 120), "Ways to Compare the Same Objects", fontsize=12)
            page.insert_text((130, 155), "PROPERTY A", fontsize=10)
            page.insert_text((360, 155), "PROPERTY B", fontsize=10)
            page.draw_rect(fitz.Rect(90, 170, 280, 300))
            page.draw_rect(fitz.Rect(330, 170, 520, 300))
            page.insert_text((115, 235), "diagram label one", fontsize=8)
            page.insert_text((355, 235), "diagram label two", fontsize=8)
            page.insert_text((180, 330), "Figure 1. Objects grouped in two ways.", fontsize=9)
            page.insert_text((72, 390), "Properties of Objects", fontsize=14)
            page.insert_text((72, 420), "Objects can be compared using observable properties.", fontsize=10)
            document.save(path)
            document.close()

            blocks = extract_pdf_text_blocks(path)
            classified = classify_instructional_blocks(blocks)
            learning_objects = build_section_learning_objects(classified, [])
            all_text = "\n".join(
                item["title"] + "\n" + item["content"]
                for item in learning_objects
            )

            self.assertTrue(any(block.get("is_figure_text") for block in classified))
            self.assertNotIn("PROPERTY A", all_text)
            self.assertNotIn("diagram label one", all_text)
            self.assertIn("Properties of Objects", all_text)
            self.assertIn("Objects can be compared using observable properties.", all_text)
        finally:
            os.unlink(path)

    def test_explanatory_pdf_sentences_are_not_mistaken_for_objectives(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Material Classification", "line_count": 1},
            {
                "block_id": 2,
                "page": 1,
                "text": "Materials placed together with similar objects form a useful classification.",
                "line_count": 1,
            },
            {
                "block_id": 3,
                "page": 1,
                "text": "Learning to notice properties helps us understand why objects are made differently.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(
            learning_objects[0]["content"],
            "Materials placed together with similar objects form a useful classification.\n"
            "Learning to notice properties helps us understand why objects are made differently.",
        )

    def test_bold_pdf_title_line_is_not_downgraded_to_document_metadata(self):
        blocks = [
            {
                "block_id": 1,
                "page": 1,
                "text": "How Flowering Plants Reproduce",
                "line_count": 1,
                "is_bold": True,
                "font_size": 14.0,
            }
        ]

        classified = classify_instructional_blocks(blocks)
        self.assertEqual(classified[0]["category"], "lesson_content")
        self.assertTrue(classified[0]["include_in_narration"])

    def test_empty_process_heading_yields_labeled_child_learning_objects(self):
        blocks = [
            {
                "block_id": 1,
                "page": 1,
                "text": "How Flowering Plants Reproduce",
                "line_count": 1,
            },
            {
                "block_id": 2,
                "page": 1,
                "text": "Pollination: Pollen is carried from the anther to the stigma.",
                "line_count": 1,
            },
            {
                "block_id": 3,
                "page": 1,
                "text": "Fertilization: After landing on the stigma, a pollen grain grows a tube down the style into the ovary.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])
        by_title = {item["title"]: item["content"] for item in learning_objects}

        self.assertNotIn("How Flowering Plants Reproduce", by_title)
        self.assertEqual(list(by_title), ["Pollination", "Fertilization"])
        self.assertEqual(by_title["Pollination"], "Pollen is carried from the anther to the stigma.")
        self.assertEqual(
            by_title["Fertilization"],
            "After landing on the stigma, a pollen grain grows a tube down the style into the ovary.",
        )

    def test_plain_heading_with_bullets_is_preserved_as_learning_object(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "What is Matter?", "line_count": 1},
            {
                "block_id": 2,
                "page": 1,
                "text": "Matter is anything that has mass and occupies space.",
                "line_count": 1,
            },
            {"block_id": 3, "page": 1, "text": "Key Characteristics of Matter", "line_count": 1},
            {
                "block_id": 4,
                "page": 1,
                "text": "• Matter has mass. • Matter occupies space (volume).",
                "line_count": 1,
            },
            {"block_id": 5, "page": 1, "text": "Examples", "line_count": 1},
            {
                "block_id": 6,
                "page": 1,
                "text": "Solid: book, chair, stone Liquid: water, milk Gas: oxygen",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])
        by_title = {item["title"]: item["content"] for item in learning_objects}

        self.assertIn("Key Characteristics of Matter", by_title)
        self.assertIn("Matter has mass", by_title["Key Characteristics of Matter"])
        self.assertIn("Examples", by_title)
        self.assertIn("Solid: book", by_title["Examples"])

    def test_inline_definition_becomes_node_without_rewriting_content(self):
        exact_definition = "A mixture is a combination of two or more materials that are physically combined."
        blocks = [
            {
                "block_id": 1,
                "page": 1,
                "text": f"Mixture: {exact_definition}",
                "line_count": 1,
            }
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])

        self.assertEqual(len(learning_objects), 1)
        self.assertEqual(learning_objects[0]["title"], "Mixture")
        self.assertEqual(learning_objects[0]["content"], exact_definition)
        self.assertEqual(learning_objects[0]["source_excerpt"], f"Mixture: {exact_definition}")

    def test_learning_objects_exclude_questions_and_activities_but_keep_notes_exactly(self):
        definition = "A mixture is a combination of two or more substances where each substance keeps its own properties."
        blocks = [
            {"block_id": 1, "page": 1, "text": "Mixtures", "line_count": 1},
            {"block_id": 2, "page": 1, "text": definition, "line_count": 1},
            {"block_id": 3, "page": 1, "text": "Questions", "line_count": 1},
            {"block_id": 4, "page": 1, "text": "1. What is a mixture?", "line_count": 1},
            {"block_id": 5, "page": 1, "text": "Activity: Mix sand and water.", "line_count": 1},
            {"block_id": 6, "page": 1, "text": "Types of Mixtures", "line_count": 1},
            {
                "block_id": 7,
                "page": 1,
                "text": "Homogeneous mixtures have a uniform appearance throughout.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])
        by_title = {item["title"]: item["content"] for item in learning_objects}

        self.assertEqual(by_title["Mixtures"], definition)
        self.assertEqual(
            by_title["Types of Mixtures"],
            "Homogeneous mixtures have a uniform appearance throughout.",
        )
        all_content = "\n".join(item["content"] for item in learning_objects)
        self.assertNotIn("What is a mixture?", all_content)
        self.assertNotIn("Mix sand and water", all_content)

    def test_activity_section_skips_numbered_prompts_until_next_topic(self):
        blocks = [
            {"block_id": 1, "page": 1, "text": "Mixtures", "line_count": 1},
            {
                "block_id": 2,
                "page": 1,
                "text": "A mixture is made when two or more materials are combined.",
                "line_count": 1,
            },
            {"block_id": 3, "page": 1, "text": "Learning Activity 1", "line_count": 1},
            {
                "block_id": 4,
                "page": 1,
                "text": "1. Classify the following materials as homogeneous or heterogeneous.",
                "line_count": 1,
            },
            {"block_id": 5, "page": 1, "text": "2. Write your answers in your notebook.", "line_count": 1},
            {"block_id": 6, "page": 1, "text": "Guide Questions", "line_count": 1},
            {"block_id": 7, "page": 1, "text": "What happened when you mixed sand and water?", "line_count": 1},
            {"block_id": 8, "page": 1, "text": "Solutions", "line_count": 1},
            {
                "block_id": 9,
                "page": 1,
                "text": "A solution is a homogeneous mixture in which one substance dissolves in another.",
                "line_count": 1,
            },
        ]

        learning_objects = build_learning_objects_from_pdf_blocks(blocks, [])
        by_title = {item["title"]: item["content"] for item in learning_objects}
        all_content = "\n".join(item["content"] for item in learning_objects)

        self.assertEqual(
            by_title["Mixtures"],
            "A mixture is made when two or more materials are combined.",
        )
        self.assertEqual(
            by_title["Solutions"],
            "A solution is a homogeneous mixture in which one substance dissolves in another.",
        )
        self.assertNotIn("Classify the following", all_content)
        self.assertNotIn("Write your answers", all_content)
        self.assertNotIn("What happened", all_content)

    def test_choose_outline_node_for_material_prefers_deeper_topic(self):
        course = CourseGroup.objects.create(title="Science 7")
        module = OutlineNode.objects.create(course=course, title="Matter", order=0, depth=0)
        topic_states = OutlineNode.objects.create(course=course, parent=module, title="States of Matter", order=0, depth=1)
        OutlineNode.objects.create(course=course, parent=module, title="Properties of Matter", order=1, depth=1)

        matched = choose_outline_node_for_material(
            course,
            "States of Matter PDF",
            "Solid, liquid, and gas are states of matter.",
        )

        self.assertEqual(matched, topic_states)

    def test_choose_outline_node_for_material_uses_tfidf_cosine_for_specific_subtopic(self):
        course = CourseGroup.objects.create(title="Science 9")
        module = OutlineNode.objects.create(course=course, title="Materials", order=0, depth=0)
        grouping = OutlineNode.objects.create(
            course=course,
            parent=module,
            title="Grouping Materials Based on Properties",
            order=0,
            depth=1,
        )
        mixtures = OutlineNode.objects.create(
            course=course,
            parent=module,
            title="Mixtures and Their Characteristics",
            order=1,
            depth=1,
        )

        matched = choose_outline_node_for_material(
            course,
            "Sorting by observable properties",
            "Learners group materials by color, texture, hardness, flexibility, and ability to absorb water.",
        )

        self.assertEqual(matched, grouping)
        self.assertNotEqual(matched, mixtures)

    def test_selected_outline_topic_still_requires_pdf_content_match(self):
        course = CourseGroup.objects.create(title="Science 7")
        module = OutlineNode.objects.create(course=course, title="Matter", order=0, depth=0)
        states = OutlineNode.objects.create(
            course=course,
            parent=module,
            title="States of Matter",
            order=0,
            depth=1,
        )
        plants = OutlineNode.objects.create(
            course=course,
            title="Plant Parts and Functions",
            order=1,
            depth=0,
        )

        valid = validate_outline_node_for_material(
            course,
            states,
            "A misleading filename about plants",
            "Solids, liquids, and gases are states of matter with different particle arrangements.",
        )
        wrong_selected_topic = validate_outline_node_for_material(
            course,
            states,
            "States of Matter",
            "Roots absorb water while leaves use sunlight to make food for a plant.",
        )
        unrelated = validate_outline_node_for_material(
            course,
            plants,
            "Plant Parts and Functions",
            "A recipe explains how to bake bread and decorate a cake.",
        )

        self.assertEqual(valid, states)
        self.assertIsNone(wrong_selected_topic)
        self.assertIsNone(unrelated)

    def test_selected_topic_accepts_lesson_when_related_sibling_scores_higher(self):
        course = CourseGroup.objects.create(title="Science 7")
        module = OutlineNode.objects.create(course=course, title="Matter", order=0, depth=0)
        characteristics = OutlineNode.objects.create(
            course=course,
            parent=module,
            title="Mixtures and Their Characteristics",
            order=0,
            depth=1,
        )
        separating = OutlineNode.objects.create(
            course=course,
            parent=module,
            title="Separating Mixtures",
            order=1,
            depth=1,
        )
        lesson_text = (
            "Mixtures have observable characteristics because their components "
            "may retain their properties. Separating mixtures can use filtration. "
            "Separating mixtures can also use evaporation or decantation. These "
            "separating methods isolate solids and liquids from mixtures."
        )

        automatic_match = choose_outline_node_for_material(course, "", lesson_text)
        selected_match = validate_outline_node_for_material(
            course,
            characteristics,
            "unreliable-filename.pdf",
            lesson_text,
        )

        self.assertEqual(automatic_match, separating)
        self.assertEqual(selected_match, characteristics)


class FinalReviewDeletionTests(TestCase):
    """Step 3 (Publish) lets a teacher drop stale questions and content."""

    def setUp(self):
        self.client = authenticated_api_client()
        self.course = CourseGroup.objects.create(title="Science")
        self.node = OutlineNode.objects.create(
            course=self.course,
            title="Properties of Materials",
            order=0,
            depth=0,
        )
        self.material = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.node,
            module_node=self.node,
            title="lesson",
            pdf_file="learning_materials/lesson.pdf",
            status=LearningMaterial.Status.COMPLETED,
            generated_json={"learning_objects_confirmed": True},
        )

    def _objects_url(self, object_id):
        return (
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}"
            f"/learning-objects/{object_id}/"
        )

    def _questions_url(self, question_id):
        return (
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}"
            f"/questions/{question_id}/"
        )

    def test_deleting_a_question_removes_it_from_the_topic(self):
        LearningObject.objects.create(
            material=self.material,
            title="Solid",
            content="A solid has a definite shape and volume.",
            order=0,
        )
        ensure_learning_object_groups(self.material)
        question = Question.objects.create(
            material=self.material,
            prompt="Which state of matter keeps its shape?",
            question_type=Question.Type.MULTIPLE_CHOICE,
            choices=["Solid", "Liquid"],
            correct_answer="Solid",
            order=0,
        )
        resources_url = (
            f"/api/courses/{self.course.id}/outline-nodes/{self.node.id}/learning-resources/"
        )
        before = self.client.get(resources_url)
        self.assertEqual(
            [item["id"] for item in before.data["question_pairings"]],
            [question.id],
        )

        response = self.client.delete(self._questions_url(question.id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Question.objects.filter(pk=question.id).exists())
        self.assertEqual(response.data["question_pairings"], [])

    def test_deleting_a_question_from_another_topic_is_rejected(self):
        other_node = OutlineNode.objects.create(
            course=self.course,
            title="Other topic",
            order=1,
            depth=0,
        )
        other_material = LearningMaterial.objects.create(
            course=self.course,
            outline_node=other_node,
            module_node=other_node,
            title="other",
            pdf_file="learning_materials/other.pdf",
            status=LearningMaterial.Status.COMPLETED,
            generated_json={"learning_objects_confirmed": True},
        )
        question = Question.objects.create(
            material=other_material,
            prompt="Unrelated?",
            question_type=Question.Type.TRUE_FALSE,
            choices=["True", "False"],
            correct_answer="True",
            order=0,
        )

        response = self.client.delete(self._questions_url(question.id))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Question.objects.filter(pk=question.id).exists())

    def test_deleting_a_learning_object_keeps_the_material_confirmed(self):
        keeper = LearningObject.objects.create(
            material=self.material,
            title="Solid",
            content="A solid has a definite shape and volume.",
            order=0,
        )
        doomed = LearningObject.objects.create(
            material=self.material,
            title="Typo duplicate",
            content="A solid has a definte shape.",
            order=1,
        )
        ensure_learning_object_groups(self.material)

        response = self.client.delete(self._objects_url(doomed.id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(LearningObject.objects.filter(pk=doomed.id).exists())
        self.material.refresh_from_db()
        self.assertTrue(self.material.generated_json["learning_objects_confirmed"])
        remaining = [
            item["id"]
            for group in response.data["learning_object_groups"]
            for item in group["learning_objects"]
        ]
        self.assertEqual(remaining, [keeper.id])

    def test_deleting_the_last_object_in_a_group_drops_the_empty_group(self):
        solo = LearningObject.objects.create(
            material=self.material,
            title="Solid",
            content="A solid has a definite shape and volume.",
            order=0,
        )
        ensure_learning_object_groups(self.material)
        group_id = LearningObject.objects.get(pk=solo.id).group_id
        self.assertIsNotNone(group_id)

        response = self.client.delete(self._objects_url(solo.id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(LearningObjectGroup.objects.filter(pk=group_id).exists())
        self.assertEqual(response.data["learning_object_groups"], [])

    def test_deleting_an_unconfirmed_learning_object_is_rejected(self):
        self.material.generated_json = {"learning_objects_confirmed": False}
        self.material.save(update_fields=["generated_json"])
        item = LearningObject.objects.create(
            material=self.material,
            title="Solid",
            content="A solid has a definite shape and volume.",
            order=0,
        )

        response = self.client.delete(self._objects_url(item.id))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(LearningObject.objects.filter(pk=item.id).exists())
