from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from unittest.mock import patch

from .models import CourseGroup, LearningMaterial, LearningObject, OutlineNode
from .serializers import LearningMaterialSerializer
from .services.content_generator import (
    _text_blocks_from_transcription,
    build_learning_objects_from_pdf_blocks,
    build_section_learning_objects,
    refine_learning_object_titles_with_llm,
)
from .services.outline_parser import (
    ParsedOutlineNode,
    _build_outline_llm_context,
    _clean_related_info,
    extract_outline_text,
    parse_outline_text,
)


class MilestoneModelSmokeTests(TestCase):
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

    def test_course_outline_upload_rejects_non_pdf(self):
        client = APIClient()
        course = CourseGroup.objects.create(title="Science 7")
        response = client.post(
            f"/api/courses/{course.id}/upload-outline/",
            {"outline_file": SimpleUploadedFile("outline.txt", b"Module 1: Matter")},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], "Only PDF course outlines are supported.")


class OutlineParserTests(TestCase):
    def _titles(self, nodes):
        return [(node.title, [child.title for child in node.children]) for node in nodes]

    @patch("lessons.services.outline_parser._extract_outline_nodes_with_llm", return_value=None)
    def test_teacher_module_bullets_stay_under_declared_module(self, _mock_llm):
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

    @patch("lessons.services.outline_parser._extract_outline_nodes_with_llm", return_value=None)
    def test_wrapped_bullet_title_does_not_absorb_learning_focus(self, _mock_llm):
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

    def test_llm_context_prefers_extracted_pdf_layout_block(self):
        text = """
        PDF LAYOUT TABLES
        TABLE page=1
        ROW 1:
          CELL 1: Session
          CELL 2: Teacher Outline
          CELL 3: Classroom Task
        ROW 2:
          CELL 1: Session 2
          CELL 2: Unit 1: Matter<br>- States of Matter
          CELL 3: Lab

        RAW PDF TEXT
        This raw fallback should not be preferred.
        """

        context = _build_outline_llm_context(text)

        self.assertIn("PDF LAYOUT TABLES", context)
        self.assertIn("Teacher Outline", context)
        self.assertNotIn("raw fallback", context.lower())

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

    @patch("lessons.services.outline_parser._extract_outline_nodes_with_llm", return_value=None)
    def test_deped_content_column_extracts_only_numbered_topics(self, _mock_llm):
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

    @patch("lessons.services.outline_parser._extract_outline_nodes_with_llm", return_value=None)
    def test_module_lesson_outline_ignores_course_info_and_nests_bullets(self, _mock_llm):
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

    @patch("lessons.services.outline_parser._extract_teacher_module_outline")
    @patch("lessons.services.outline_parser._extract_outline_nodes_with_llm")
    def test_text_outline_uses_llm_before_parser(self, mock_llm, mock_parser):
        mock_llm.return_value = [
            ParsedOutlineNode(
                title="Matter",
                depth=0,
                order=0,
                related_info={"notes": ["Teacher context"]},
            )
        ]

        nodes = parse_outline_text("Module 1: Matter")

        self.assertEqual(nodes[0].title, "Matter")
        self.assertEqual(nodes[0].related_info, {"notes": ["Teacher context"]})
        mock_parser.assert_not_called()

    @patch("lessons.services.llm_client.get_llm_client")
    def test_llm_outline_extraction_rejects_fragments_and_non_topics(self, mock_get_client):
        mock_client = mock_get_client.return_value
        mock_client.generate_text.return_value = {
            "text": """
            {
              "nodes": [
                {"source_id": 1, "title": "Invented Matter Title", "level": 0, "order": 1, "related_info": {"notes": ["ignored"]}},
                {"source_id": 999, "title": "Invented Topic", "level": 1, "order": 2},
                {"source_id": 2, "title": "Paraphrased Properties", "level": 1, "order": 3}
              ]
            }
            """
        }

        text = """
        Course Outline
        Module 1: Matter
        - Properties
        Learning focus: Describe physical and chemical properties.
        Quiz
        """

        nodes = parse_outline_text(text)

        self.assertEqual([(node.title, [child.title for child in node.children]) for node in nodes], [
            ("Matter", ["Properties"])
        ])
        self.assertEqual(nodes[0].related_info, {})
        prompt = mock_client.generate_text.call_args.args[0]
        self.assertIn("You may choose only from those candidate line IDs.", prompt)
        self.assertIn("Module 1: Matter", prompt)

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


class LearningObjectPreservationTests(TestCase):
    def test_transcribed_page_text_becomes_learning_blocks(self):
        blocks = _text_blocks_from_transcription(
            "Page 1\nWhat is Matter?\nMatter has mass and occupies space."
        )

        self.assertEqual([block["text"] for block in blocks], ["What is Matter?", "Matter has mass and occupies space."])
        self.assertEqual(blocks[0]["source"], "vision_page_transcription")

    def test_image_learning_object_includes_visible_text(self):
        learning_objects = build_section_learning_objects(
            [],
            [
                {
                    "index": 0,
                    "page_number": 1,
                    "description": "Diagram showing the water cycle.",
                    "visible_text": "Evaporation, Condensation, Precipitation",
                }
            ],
        )

        self.assertIn("Diagram showing the water cycle.", learning_objects[0]["content"])
        self.assertIn("Visible text: Evaporation, Condensation, Precipitation", learning_objects[0]["content"])

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

    @patch("lessons.services.content_generator.get_llm_client")
    def test_llm_makes_vague_learning_object_titles_standalone(self, mock_get_client):
        mock_client = mock_get_client.return_value
        mock_client.generate_text.return_value = {
            "text": """
            {
              "items": [
                {"index": 0, "title": "Examples of solids, liquids, and gases"}
              ]
            }
            """
        }
        learning_objects = [
            {
                "order": 0,
                "title": "Examples",
                "type": "lesson_content",
                "content": "Solid: book, chair, stone Liquid: water, milk Gas: oxygen",
            }
        ]

        reviewed = refine_learning_object_titles_with_llm(learning_objects, lesson_title="States of Matter")

        self.assertEqual(reviewed[0]["title"], "Examples of solids, liquids, and gases")
        self.assertEqual(reviewed[0]["content"], learning_objects[0]["content"])
        prompt = mock_client.generate_text.call_args.args[0]
        self.assertIn("Make each learning object title understandable as a standalone card title", prompt)
