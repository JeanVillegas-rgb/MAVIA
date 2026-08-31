from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from unittest.mock import patch

from user.models import User

from .models import CourseGroup, LearningMaterial, LearningObject, OutlineNode
from .serializers import LearningMaterialSerializer
from .services.content_generator import (
    _text_blocks_from_transcription,
    build_learning_objects_from_pdf_blocks,
    build_narration_script_from_learning_objects,
    build_section_learning_objects,
    choose_outline_node_for_material,
)
from .services.audio_generator import generate_material_audio_playlist
from .services.instructional_content_classifier import classify_instructional_blocks, extract_pdf_text_blocks
from .services.outline_parser import (
    ParsedOutlineNode,
    _build_outline_candidates,
    _clean_related_info,
    _extract_pdf_table_outline_text,
    _looks_like_plain_outline_title_start,
    extract_outline_text,
    parse_outline_text,
)


def _teacher_client():
    """CourseGroupViewSet is teacher/admin-gated; tests need an authenticated
    client to reach it, not just a course/material fixture."""
    client = APIClient()
    teacher = User.objects.create_user(
        username=f"teacher{User.objects.count()}",
        password="pass1234",
        role=User.Role.TEACHER,
    )
    client.force_authenticate(user=teacher)
    return client


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

    def test_course_outline_upload_rejects_non_pdf(self):
        client = _teacher_client()
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
        client = _teacher_client()
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
        client = _teacher_client()
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
        client = _teacher_client()
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
        client = _teacher_client()
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
