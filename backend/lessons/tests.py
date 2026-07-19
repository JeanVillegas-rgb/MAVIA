import json
import os

from django.urls import reverse
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.test import APITestCase
from unittest.mock import patch

from .models import (
    ConceptPrerequisiteEdge,
    ConceptSource,
    CourseGroup,
    ExtractedConcept,
    LearningMaterial,
    LearningObject,
    LearningObjectPrerequisiteEdge,
    ModuleConceptDAGState,
    OutlineEdge,
    OutlineNode,
)
from .services.concept_dag_state import confirm_module_dag
from .services.concept_edge_generator import generate_module_concept_dag, _title_overlap_score
from .services.learning_object_edge_generator import generate_module_learning_object_dag, is_learning_object_dag_acyclic
from .services.edge_generator import (
    EDGE_SCORE_THRESHOLD,
    MAX_ANCESTOR_DEPTH_GAP,
    MAX_FORWARD_DISTANCE,
    MAX_INCOMING_EDGES_PER_NODE,
    MAX_OUTGOING_EDGES_PER_NODE,
    compute_semantic_similarity,
    generate_prerequisite_edges,
)
from .services.module_resolver import get_concept_module, get_material_module, get_top_level_module
from .services.outline_parser import ParsedOutlineNode, parse_outline_text
from .services.content_generator import (
    apply_classification_override,
    build_fallback_learning_objects_from_text,
    build_learning_objects_from_narration,
    build_lesson_playlist,
    build_narration_script_from_learning_objects,
    build_section_learning_objects,
    build_teacher_text_narration,
    generate_material_outputs,
    review_learning_objects_for_bvi_learners,
)
from .services.audio_generator import generate_material_audio_playlist
from .services.instructional_content_classifier import classify_instructional_blocks


def flatten_parsed_titles(nodes):
    titles = []
    for node in nodes:
        titles.append(node.title)
        titles.extend(flatten_parsed_titles(node.children))
    return titles


class ConceptDAGModelTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Dynamic Course")
        self.module = OutlineNode.objects.create(
            course=self.course,
            title="Dynamic Module",
            depth=0,
            order=0,
        )
        self.child = OutlineNode.objects.create(
            course=self.course,
            parent=self.module,
            title="Dynamic Lesson Area",
            depth=1,
            order=0,
        )
        self.other_course = CourseGroup.objects.create(title="Other Course")
        self.other_module = OutlineNode.objects.create(
            course=self.other_course,
            title="Other Module",
            depth=0,
            order=0,
        )
        self.material = LearningMaterial.objects.create(
            course=self.course,
            module_node=self.module,
            title="Dynamic Material",
            pdf_file=SimpleUploadedFile(
                "dynamic.pdf",
                b"%PDF-1.4 fake pdf content",
                content_type="application/pdf",
            ),
        )

    def create_concept(self, title="Dynamic Concept", module=None, order=0):
        return ExtractedConcept.objects.create(
            course=self.course,
            module_node=module or self.module,
            canonical_title=title,
            description="A teachable concept extracted from uploaded material.",
            order=order,
            confidence=0.85,
        )

    def test_learning_material_module_node_must_be_top_level_and_same_course(self):
        material = LearningMaterial(
            course=self.course,
            module_node=self.child,
            title="Invalid Child Module",
            pdf_file=SimpleUploadedFile("child.pdf", b"pdf", content_type="application/pdf"),
        )

        with self.assertRaises(ValidationError):
            material.full_clean()

        other_material = LearningMaterial(
            course=self.course,
            module_node=self.other_module,
            title="Invalid Other Course Module",
            pdf_file=SimpleUploadedFile("other.pdf", b"pdf", content_type="application/pdf"),
        )

        with self.assertRaises(ValidationError):
            other_material.full_clean()

    def test_extracted_concept_requires_top_level_module_from_same_course(self):
        invalid_child_concept = ExtractedConcept(
            course=self.course,
            module_node=self.child,
            canonical_title="Invalid Child Concept",
        )

        with self.assertRaises(ValidationError):
            invalid_child_concept.full_clean()

        invalid_course_concept = ExtractedConcept(
            course=self.course,
            module_node=self.other_module,
            canonical_title="Invalid Course Concept",
        )

        with self.assertRaises(ValidationError):
            invalid_course_concept.full_clean()

    def test_concept_confidence_must_be_between_zero_and_one(self):
        concept = ExtractedConcept(
            course=self.course,
            module_node=self.module,
            canonical_title="Confidence Concept",
            confidence=1.5,
        )

        with self.assertRaises(ValidationError):
            concept.full_clean()

    def test_normalized_concept_title_is_unique_only_inside_same_module(self):
        self.create_concept("Particle Arrangement")

        with self.assertRaises(ValidationError):
            ExtractedConcept(
                course=self.course,
                module_node=self.module,
                canonical_title=" particle-arrangement ",
            ).full_clean()

        other_module = OutlineNode.objects.create(
            course=self.course,
            title="Second Dynamic Module",
            depth=0,
            order=1,
        )
        duplicate_in_other_module = ExtractedConcept.objects.create(
            course=self.course,
            module_node=other_module,
            canonical_title="Particle Arrangement",
        )

        self.assertEqual(duplicate_in_other_module.normalized_title, "particle arrangement")

    def test_concept_source_must_match_concept_course_and_module(self):
        concept = self.create_concept()
        valid_source = ConceptSource(
            concept=concept,
            learning_material=self.material,
            source_excerpt="A short excerpt from the uploaded material.",
            first_appearance_order=1,
        )
        valid_source.full_clean()

        other_module = OutlineNode.objects.create(
            course=self.course,
            title="Other Dynamic Module",
            depth=0,
            order=1,
        )
        other_material = LearningMaterial.objects.create(
            course=self.course,
            module_node=other_module,
            title="Other Dynamic Material",
            pdf_file=SimpleUploadedFile("other.pdf", b"pdf", content_type="application/pdf"),
        )
        invalid_source = ConceptSource(concept=concept, learning_material=other_material)

        with self.assertRaises(ValidationError):
            invalid_source.full_clean()

    def test_concept_prerequisite_edge_validates_module_course_scores_and_self_edge(self):
        source = self.create_concept("Source Concept", order=0)
        target = self.create_concept("Target Concept", order=1)
        edge = ConceptPrerequisiteEdge(
            course=self.course,
            module_node=self.module,
            source=source,
            target=target,
            score=0.80,
            semantic_similarity=0.75,
            instructional_order_score=1.0,
        )
        edge.full_clean()

        self_edge = ConceptPrerequisiteEdge(
            course=self.course,
            module_node=self.module,
            source=source,
            target=source,
        )
        with self.assertRaises(ValidationError):
            self_edge.full_clean()

        invalid_score_edge = ConceptPrerequisiteEdge(
            course=self.course,
            module_node=self.module,
            source=source,
            target=target,
            score=1.2,
        )
        with self.assertRaises(ValidationError):
            invalid_score_edge.full_clean()

    def test_concept_edge_cannot_cross_modules(self):
        source = self.create_concept("Module One Concept", order=0)
        other_module = OutlineNode.objects.create(
            course=self.course,
            title="Other Module In Same Course",
            depth=0,
            order=1,
        )
        target = ExtractedConcept.objects.create(
            course=self.course,
            module_node=other_module,
            canonical_title="Module Two Concept",
        )
        edge = ConceptPrerequisiteEdge(
            course=self.course,
            module_node=self.module,
            source=source,
            target=target,
        )

        with self.assertRaises(ValidationError):
            edge.full_clean()

    def test_legacy_outline_edge_data_still_works(self):
        edge = OutlineEdge.objects.create(
            course=self.course,
            source=self.module,
            target=self.child,
            score=0.7,
        )

        self.assertEqual(str(edge), "Dynamic Module -> Dynamic Lesson Area")


class ModuleConceptLearnerPathTests(APITestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.module = OutlineNode.objects.create(course=self.course, title="Matter", depth=0, order=0)
        self.topic = OutlineNode.objects.create(
            course=self.course,
            parent=self.module,
            title="States Topic",
            depth=1,
            order=0,
        )
        self.other_module = OutlineNode.objects.create(course=self.course, title="Force", depth=0, order=1)
        self.material_one = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.topic,
            module_node=self.module,
            title="Matter One",
            pdf_file=SimpleUploadedFile("matter-one.pdf", b"pdf", content_type="application/pdf"),
        )
        self.material_two = LearningMaterial.objects.create(
            course=self.course,
            module_node=self.module,
            title="Matter Two",
            pdf_file=SimpleUploadedFile("matter-two.pdf", b"pdf", content_type="application/pdf"),
        )

    def create_concept(self, title, module=None, order=0, material=None, excerpt=None, status_value=None):
        concept = ExtractedConcept.objects.create(
            course=self.course,
            module_node=module or self.module,
            canonical_title=title,
            description=f"{title} instructional concept.",
            order=order,
            validation_status=status_value or ExtractedConcept.ValidationStatus.APPROVED,
        )
        ConceptSource.objects.create(
            concept=concept,
            learning_material=material or self.material_one,
            page_number=order + 1,
            source_excerpt=excerpt or f"{title} source evidence.",
            first_appearance_order=order,
        )
        return concept

    def edge_url(self, edge):
        return reverse(
            "course-module-concept-edge-detail",
            kwargs={"pk": self.course.pk, "module_id": self.module.pk, "edge_id": edge.pk},
        )

    def test_module_resolution_through_nested_nodes_materials_and_concepts(self):
        concept = self.create_concept("Resolved Concept", order=0)

        self.assertEqual(get_top_level_module(self.topic), self.module)
        self.assertEqual(get_material_module(self.material_one), self.module)
        self.assertEqual(get_concept_module(concept), self.module)

    def test_manual_edges_validate_same_module_self_loop_duplicate_and_cycles(self):
        a = self.create_concept("Concept A", order=0)
        b = self.create_concept("Concept B", order=1)
        c = self.create_concept("Concept C", order=2)
        other = self.create_concept("Other Module Concept", module=self.other_module, order=0)

        url = reverse("course-create-module-concept-edge", kwargs={"pk": self.course.pk, "module_id": self.module.pk})
        response = self.client.post(url, {"source": a.id, "target": b.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        duplicate = self.client.post(url, {"source": a.id, "target": b.id}, format="json")
        self.assertEqual(duplicate.status_code, status.HTTP_400_BAD_REQUEST)

        self_loop = self.client.post(url, {"source": a.id, "target": a.id}, format="json")
        self.assertEqual(self_loop.status_code, status.HTTP_400_BAD_REQUEST)

        cross_module = self.client.post(url, {"source": a.id, "target": other.id}, format="json")
        self.assertEqual(cross_module.status_code, status.HTTP_400_BAD_REQUEST)

        self.client.post(url, {"source": b.id, "target": c.id}, format="json")
        cycle = self.client.post(url, {"source": c.id, "target": a.id}, format="json")
        self.assertEqual(cycle.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cycle", str(cycle.data).lower())

    def test_branching_and_merging_manual_edges_are_allowed(self):
        a = self.create_concept("Branch Source", order=0)
        b = self.create_concept("Branch Left", order=1)
        c = self.create_concept("Branch Right", order=2)
        d = self.create_concept("Merge Target", order=3)
        url = reverse("course-create-module-concept-edge", kwargs={"pk": self.course.pk, "module_id": self.module.pk})

        for source, target in [(a, b), (a, c), (b, d), (c, d)]:
            response = self.client.post(url, {"source": source.id, "target": target.id}, format="json")
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertEqual(ConceptPrerequisiteEdge.objects.filter(module_node=self.module).count(), 4)

    @patch("lessons.services.concept_edge_generator.compute_concept_semantic_similarity", return_value=0.95)
    def test_generator_connects_same_module_concepts_across_different_pdfs_with_provenance(self, _semantic):
        states = self.create_concept(
            "States of Matter",
            order=0,
            material=self.material_one,
            excerpt="States of Matter explains solids liquids and gases.",
        )
        grouping = self.create_concept(
            "Grouping Materials by State",
            order=1,
            material=self.material_two,
            excerpt="Grouping Materials by State depends on knowledge of States of Matter.",
        )

        summary = generate_module_concept_dag(self.course, self.module)

        self.assertEqual(summary["edges_created"], 1)
        edge = ConceptPrerequisiteEdge.objects.get(source=states, target=grouping)
        self.assertGreaterEqual(edge.score, summary["threshold"])
        self.assertIsNotNone(edge.dependency_cue_score)
        self.assertIsNotNone(edge.source_order_score)
        self.assertIsNotNone(edge.title_overlap_score)
        self.assertIn("Scores:", edge.explanation)
        self.assertIn("top_rejected_candidates", summary)
        self.assertEqual(summary["approved_concepts_considered"], 2)

    @patch("lessons.services.concept_edge_generator.compute_concept_semantic_similarity", return_value=0.95)
    def test_definition_foundation_can_connect_to_classification_application(self, _semantic):
        properties = self.create_concept(
            "Observable Properties",
            order=0,
            excerpt="Observable Properties are characteristics that can be used to describe objects.",
        )
        grouping = self.create_concept(
            "Grouping by Observable Properties",
            order=1,
            excerpt="Grouping by Observable Properties asks learners to classify objects by characteristics.",
        )

        summary = generate_module_concept_dag(self.course, self.module)

        self.assertTrue(ConceptPrerequisiteEdge.objects.filter(source=properties, target=grouping).exists())
        edge = ConceptPrerequisiteEdge.objects.get(source=properties, target=grouping)
        self.assertGreaterEqual(edge.score, summary["score_configuration"]["minimum_final_score"])
        self.assertGreater(edge.dependency_cue_score or 0, 0)

    @patch("lessons.services.concept_edge_generator.compute_concept_semantic_similarity", return_value=0.95)
    def test_high_semantic_similarity_without_direction_does_not_create_edge(self, _semantic):
        self.create_concept("Round Objects", order=0, excerpt="Round objects can be observed in the classroom.")
        self.create_concept("Smooth Objects", order=1, excerpt="Smooth objects can be observed in the classroom.")

        summary = generate_module_concept_dag(self.course, self.module)

        self.assertEqual(ConceptPrerequisiteEdge.objects.filter(module_node=self.module).count(), 0)
        self.assertGreater(summary["edges_skipped_for_insufficient_direction"], 0)
        self.assertEqual(summary["top_rejected_candidates"][0]["rejection_reason"], "insufficient_directional_evidence")

    @patch("lessons.services.concept_edge_generator.compute_concept_semantic_similarity", return_value=0.95)
    def test_generator_does_not_create_pure_sequential_chain(self, _semantic):
        self.create_concept("Alpha", order=0, excerpt="Alpha is a standalone idea.")
        self.create_concept("Beta", order=1, excerpt="Beta is a standalone idea.")
        self.create_concept("Gamma", order=2, excerpt="Gamma is a standalone idea.")

        summary = generate_module_concept_dag(self.course, self.module)

        self.assertEqual(ConceptPrerequisiteEdge.objects.filter(module_node=self.module).count(), 0)
        self.assertGreater(summary["edges_skipped_without_direction"] + summary["edges_skipped_below_threshold"], 0)

    @patch("lessons.services.concept_edge_generator.compute_concept_semantic_similarity", return_value=0.20)
    def test_weak_unrelated_concepts_remain_disconnected_with_reason(self, _semantic):
        self.create_concept("Measuring Length", order=0, excerpt="Length can be measured with a ruler.")
        self.create_concept("Plant Needs", order=1, excerpt="Plants need sunlight and water.")

        summary = generate_module_concept_dag(self.course, self.module)

        self.assertEqual(ConceptPrerequisiteEdge.objects.filter(module_node=self.module).count(), 0)
        self.assertEqual(summary["top_rejected_candidates"][0]["rejection_reason"], "insufficient_semantic_relationship")

    def test_generic_common_words_do_not_inflate_title_overlap(self):
        source = ExtractedConcept(
            course=self.course,
            module_node=self.module,
            canonical_title="Matter Science Lesson",
            description="",
        )
        target = ExtractedConcept(
            course=self.course,
            module_node=self.module,
            canonical_title="Material Topic Activity",
            description="",
        )

        self.assertEqual(_title_overlap_score(source, target), 0.0)

    @patch.dict(os.environ, {"ENABLE_CONCEPT_EDGE_LLM_REVIEW": "False"})
    @patch("lessons.services.concept_edge_generator.compute_concept_semantic_similarity", return_value=0.95)
    def test_generator_is_deterministic_when_optional_llm_review_disabled(self, _semantic):
        self.create_concept(
            "Basic Characteristics",
            order=0,
            excerpt="Basic characteristics describe observable features.",
        )
        self.create_concept(
            "Classifying by Characteristics",
            order=1,
            excerpt="Classifying by characteristics groups objects using observable features.",
        )

        first = generate_module_concept_dag(self.course, self.module)
        ConceptPrerequisiteEdge.objects.filter(module_node=self.module).delete()
        second = generate_module_concept_dag(self.course, self.module)

        self.assertEqual(first["edges_created"], second["edges_created"])
        self.assertEqual(first["top_rejected_candidates"], second["top_rejected_candidates"])

    @patch("lessons.services.concept_edge_generator.compute_concept_semantic_similarity", return_value=0.95)
    def test_regeneration_affects_only_selected_module_and_preserves_manual_edges(self, _semantic):
        a = self.create_concept("Foundation Concept", order=0, excerpt="Foundation Concept source.")
        b = self.create_concept("Dependent Concept", order=1, excerpt="Dependent Concept depends on Foundation Concept.")
        manual = ConceptPrerequisiteEdge.objects.create(
            course=self.course,
            module_node=self.module,
            source=a,
            target=b,
            score=1.0,
            validation_status=ConceptPrerequisiteEdge.ValidationStatus.APPROVED,
            is_manual=True,
            explanation="Teacher approved.",
        )
        other_a = self.create_concept("Other A", module=self.other_module, order=0)
        other_b = self.create_concept("Other B", module=self.other_module, order=1)
        other_edge = ConceptPrerequisiteEdge.objects.create(
            course=self.course,
            module_node=self.other_module,
            source=other_a,
            target=other_b,
            score=0.7,
        )

        generate_module_concept_dag(self.course, self.module)

        self.assertTrue(ConceptPrerequisiteEdge.objects.filter(pk=manual.pk).exists())
        self.assertTrue(ConceptPrerequisiteEdge.objects.filter(pk=other_edge.pk).exists())

    def test_concept_dag_api_returns_only_selected_module_concepts_and_edges(self):
        a = self.create_concept("Selected A", order=0)
        b = self.create_concept("Selected B", order=1)
        other = self.create_concept("Other Module API Concept", module=self.other_module, order=0)
        edge = ConceptPrerequisiteEdge.objects.create(
            course=self.course,
            module_node=self.module,
            source=a,
            target=b,
            score=0.8,
            dependency_cue_score=1.0,
            source_order_score=1.0,
            title_overlap_score=0.2,
            explanation="API test edge.",
        )
        ConceptPrerequisiteEdge.objects.create(
            course=self.course,
            module_node=self.other_module,
            source=other,
            target=self.create_concept("Other Module API Target", module=self.other_module, order=1),
            score=0.8,
        )

        response = self.client.get(
            reverse("course-module-concept-dag", kwargs={"pk": self.course.pk, "module_id": self.module.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual({node["id"] for node in response.data["nodes"]}, {a.id, b.id})
        self.assertEqual([item["id"] for item in response.data["edges"]], [edge.id])
        self.assertIn("dependency_cue_score", response.data["edges"][0])

    def test_confirmation_is_invalidated_after_meaningful_graph_change(self):
        a = self.create_concept("Confirm A", order=0)
        b = self.create_concept("Confirm B", order=1)
        confirm_module_dag(self.course, self.module)
        self.assertTrue(ModuleConceptDAGState.objects.get(module_node=self.module).is_confirmed)

        response = self.client.post(
            reverse("course-create-module-concept-edge", kwargs={"pk": self.course.pk, "module_id": self.module.pk}),
            {"source": a.id, "target": b.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        state = ModuleConceptDAGState.objects.get(module_node=self.module)
        self.assertFalse(state.is_confirmed)
        self.assertIn("edge", state.invalidation_reason)


class ModuleLearningObjectLearnerPathTests(APITestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.module = OutlineNode.objects.create(course=self.course, title="Matter", depth=0, order=0)
        self.topic_one = OutlineNode.objects.create(course=self.course, parent=self.module, title="States", depth=1, order=0)
        self.topic_two = OutlineNode.objects.create(course=self.course, parent=self.module, title="Grouping", depth=1, order=1)
        self.material_one = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.topic_one,
            module_node=self.module,
            title="States PDF",
            pdf_file=SimpleUploadedFile("states.pdf", b"pdf", content_type="application/pdf"),
        )
        self.material_two = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.topic_two,
            module_node=self.module,
            title="Grouping PDF",
            pdf_file=SimpleUploadedFile("grouping.pdf", b"pdf", content_type="application/pdf"),
        )

    def create_object(self, material, title, content, order):
        return LearningObject.objects.create(
            material=material,
            kind=LearningObject.Kind.TEXT,
            title=title,
            content=content,
            order=order,
        )

    def test_manual_learning_object_edges_reject_cycles(self):
        a = self.create_object(self.material_one, "Matter", "Matter has mass and occupies space.", 0)
        b = self.create_object(self.material_one, "Solid", "A solid has a definite shape and volume.", 1)
        c = self.create_object(self.material_two, "Grouping Materials", "Materials are grouped using properties.", 0)
        url = reverse("course-create-module-learning-object-edge", kwargs={"pk": self.course.pk, "module_id": self.module.pk})

        first = self.client.post(url, {"source": a.id, "target": b.id}, format="json")
        second = self.client.post(url, {"source": b.id, "target": c.id}, format="json")
        cycle = self.client.post(url, {"source": c.id, "target": a.id}, format="json")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(cycle.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cycle", str(cycle.data).lower())
        self.assertTrue(is_learning_object_dag_acyclic(self.module))

    @patch("lessons.services.learning_object_edge_generator.compute_learning_object_semantic_similarity", return_value=0.90)
    def test_learning_objects_are_dag_nodes_and_can_connect_across_pdfs(self, _semantic):
        source = self.create_object(
            self.material_one,
            "Matter",
            "Matter is anything that has mass and occupies space.",
            0,
        )
        target = self.create_object(
            self.material_two,
            "Grouping Materials",
            "Learners classify and group materials using observable properties of matter.",
            0,
        )

        summary = generate_module_learning_object_dag(self.course, self.module)

        self.assertEqual(summary["learning_objects_considered"], 2)
        self.assertEqual(summary["cross_pdf_pairs_evaluated"], 1)
        self.assertTrue(LearningObjectPrerequisiteEdge.objects.filter(source=source, target=target).exists())

    @patch("lessons.services.learning_object_edge_generator.compute_learning_object_semantic_similarity", return_value=0.90)
    def test_source_order_alone_does_not_create_learning_object_edge(self, _semantic):
        self.create_object(self.material_one, "Blue Examples", "Blue examples are shown.", 0)
        self.create_object(self.material_two, "Red Examples", "Red examples are shown.", 0)

        summary = generate_module_learning_object_dag(self.course, self.module)

        self.assertEqual(LearningObjectPrerequisiteEdge.objects.filter(module_node=self.module).count(), 0)
        self.assertGreater(
            summary["edges_skipped_for_insufficient_direction"] + summary["edges_skipped_below_threshold"],
            0,
        )


class OutlineParserTests(TestCase):
    @patch("lessons.services.llm_client.get_llm_client")
    @patch("lessons.services.outline_parser._extract_outline_nodes_with_llm", return_value=None)
    def test_syllabus_schedule_rows_are_not_saved_as_topics(self, _llm, mock_client_factory):
        mock_client_factory.return_value.generate_text.return_value = None
        text = """
        Week 1
        expectation, Self-introduction,
        Introduction to the Course syllabus
        K to 12
        Class discussion; short
        Week 2
        Module 1: Properties of Matter
        Week 3
        Module 1: Properties of Matter
        Week 4
        Module 2: Changes that Materials Undergo
        Group discussion;
        5-6
        Week 7
        Module 3: Parts and Functions of the Human Body
        """

        titles = flatten_parsed_titles(parse_outline_text(text))

        self.assertEqual(
            titles,
            [
                "Properties of Matter",
                "Changes that Materials Undergo",
                "Parts and Functions of the Human Body",
            ],
        )
        self.assertNotIn("Week 1", titles)
        self.assertNotIn("Week 7", titles)
        self.assertNotIn("Class discussion; short", titles)
        self.assertNotIn("Group discussion", titles)

    @patch("lessons.services.llm_client.get_llm_client")
    @patch("lessons.services.outline_parser._extract_outline_nodes_with_llm")
    def test_llm_topic_classifier_removes_unlisted_noise(self, mock_extract, mock_client_factory):
        os.environ["OLLAMA_VALIDATE_OUTLINE"] = "True"
        self.addCleanup(lambda: os.environ.pop("OLLAMA_VALIDATE_OUTLINE", None))
        mock_extract.return_value = [
            ParsedOutlineNode(title="Morning Classroom Orientation", depth=0, order=0),
            ParsedOutlineNode(title="Photosynthesis", depth=0, order=1),
            ParsedOutlineNode(title="Plant Reproduction", depth=0, order=2),
        ]
        mock_client_factory.return_value.generate_text.return_value = {"text": json.dumps({
            "items": [
                {
                    "title": "Morning Classroom Orientation",
                    "is_topic": False,
                    "reason": "Routine/admin orientation, not subject content.",
                },
                {
                    "title": "Photosynthesis",
                    "is_topic": True,
                    "reason": "Science concept.",
                },
                {
                    "title": "Plant Reproduction",
                    "is_topic": True,
                    "reason": "Science concept.",
                },
            ]
        })}

        titles = flatten_parsed_titles(parse_outline_text("Morning orientation then plant science topics."))

        self.assertEqual(titles, ["Photosynthesis", "Plant Reproduction"])

    @patch("lessons.services.llm_client.get_llm_client")
    @patch("lessons.services.outline_parser._extract_outline_nodes_with_llm")
    def test_topic_classifier_falls_back_when_llm_unavailable(self, mock_extract, mock_client_factory):
        mock_client_factory.return_value.generate_text.return_value = None
        mock_extract.return_value = [
            ParsedOutlineNode(title="Photosynthesis", depth=0, order=0),
        ]

        titles = flatten_parsed_titles(parse_outline_text("Photosynthesis"))

        self.assertEqual(titles, ["Photosynthesis"])


class CourseDAGAPITests(APITestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 8 Science")
        self.matter = OutlineNode.objects.create(
            course=self.course,
            title="Matter",
            depth=0,
            order=0,
        )
        self.states = OutlineNode.objects.create(
            course=self.course,
            parent=self.matter,
            title="States of Matter",
            depth=1,
            order=1,
        )
        self.changes = OutlineNode.objects.create(
            course=self.course,
            parent=self.matter,
            title="Changes in Matter",
            depth=1,
            order=2,
        )
        self.matter_to_states = OutlineEdge.objects.create(
            course=self.course,
            source=self.matter,
            target=self.states,
            score=0.92,
            semantic_similarity=0.88,
            outline_order_score=1.0,
            explanation="Matter introduces the concept before states of matter.",
        )
        self.states_to_changes = OutlineEdge.objects.create(
            course=self.course,
            source=self.states,
            target=self.changes,
            score=0.81,
            semantic_similarity=0.76,
            outline_order_score=1.0,
            explanation="States of matter should be known before changes in matter.",
        )

        self.other_course = CourseGroup.objects.create(title="Other Science")
        other_source = OutlineNode.objects.create(
            course=self.other_course,
            title="Force",
            depth=0,
            order=0,
        )
        other_target = OutlineNode.objects.create(
            course=self.other_course,
            parent=other_source,
            title="Motion",
            depth=1,
            order=1,
        )
        OutlineEdge.objects.create(
            course=self.other_course,
            source=other_source,
            target=other_target,
            score=0.75,
            explanation="Separate course edge.",
        )

        self.url = reverse("course-dag", kwargs={"pk": self.course.pk})

    def test_dag_endpoint_returns_nodes_and_edges(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("nodes", response.data)
        self.assertIn("edges", response.data)

    def test_dag_nodes_include_expected_fields(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["nodes"]), 3)

        node = response.data["nodes"][0]
        self.assertEqual(
            set(node.keys()),
            {"id", "title", "depth", "order", "parent"},
        )

    def test_dag_edges_include_expected_fields(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["edges"]), 2)

        edge = response.data["edges"][0]
        self.assertEqual(
            set(edge.keys()),
            {
                "id",
                "source",
                "target",
                "score",
                "semantic_similarity",
                "outline_order_score",
                "validation_status",
                "is_manual",
                "explanation",
            },
        )

    def test_dag_endpoint_returns_only_requested_course_records(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        node_ids = {node["id"] for node in response.data["nodes"]}
        edge_ids = {edge["id"] for edge in response.data["edges"]}

        self.assertEqual(node_ids, {self.matter.id, self.states.id, self.changes.id})
        self.assertEqual(edge_ids, {self.matter_to_states.id, self.states_to_changes.id})

    def test_dag_endpoint_returns_404_for_nonexistent_course(self):
        response = self.client.get(reverse("course-dag", kwargs={"pk": 999999}))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CourseOutlineNodeMutationAPITests(APITestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 8 Science")
        self.root = OutlineNode.objects.create(
            course=self.course,
            title="Matter",
            depth=0,
            order=0,
        )
        self.other_course = CourseGroup.objects.create(title="Other Science")
        self.other_node = OutlineNode.objects.create(
            course=self.other_course,
            title="Force",
            depth=0,
            order=0,
        )

    def test_teacher_can_create_top_level_topic(self):
        response = self.client.post(
            reverse("course-create-outline-node", kwargs={"pk": self.course.pk}),
            {"title": "Properties of Matter", "parent": None},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(OutlineNode.objects.filter(course=self.course, title="Properties of Matter").exists())
        self.assertIn("hierarchy", response.data)

    def test_teacher_can_create_child_topic(self):
        response = self.client.post(
            reverse("course-create-outline-node", kwargs={"pk": self.course.pk}),
            {"title": "States of Matter", "parent": self.root.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        child = OutlineNode.objects.get(course=self.course, title="States of Matter")
        self.assertEqual(child.parent_id, self.root.id)
        self.assertEqual(child.depth, 1)

    def test_teacher_can_rename_topic(self):
        response = self.client.patch(
            reverse(
                "course-outline-node-detail",
                kwargs={"pk": self.course.pk, "node_id": self.root.pk},
            ),
            {"title": "Properties and States of Matter"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.root.refresh_from_db()
        self.assertEqual(self.root.title, "Properties and States of Matter")

    def test_teacher_can_delete_topic(self):
        child = OutlineNode.objects.create(
            course=self.course,
            parent=self.root,
            title="States of Matter",
            depth=1,
            order=0,
        )

        response = self.client.delete(
            reverse(
                "course-outline-node-detail",
                kwargs={"pk": self.course.pk, "node_id": self.root.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(OutlineNode.objects.filter(pk=self.root.pk).exists())
        self.assertFalse(OutlineNode.objects.filter(pk=child.pk).exists())

    def test_cannot_use_parent_from_another_course(self):
        response = self.client.post(
            reverse("course-create-outline-node", kwargs={"pk": self.course.pk}),
            {"title": "Invalid Child", "parent": self.other_node.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(OutlineNode.objects.filter(course=self.course, title="Invalid Child").exists())

    def test_node_from_another_course_returns_404(self):
        response = self.client.patch(
            reverse(
                "course-outline-node-detail",
                kwargs={"pk": self.course.pk, "node_id": self.other_node.pk},
            ),
            {"title": "Should Not Change"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.other_node.refresh_from_db()
        self.assertEqual(self.other_node.title, "Force")


class CourseMaterialUploadTopicTests(APITestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 8 Science")
        self.node = OutlineNode.objects.create(
            course=self.course,
            title="States of Matter",
            depth=1,
            order=0,
        )
        self.other_course = CourseGroup.objects.create(title="Other Science")
        self.other_node = OutlineNode.objects.create(
            course=self.other_course,
            title="Motion",
            depth=0,
            order=0,
        )
        self.url = reverse("course-upload-material", kwargs={"pk": self.course.pk})

    def pdf_upload(self):
        return SimpleUploadedFile(
            "states.pdf",
            b"%PDF-1.4 fake pdf content",
            content_type="application/pdf",
        )

    @patch("lessons.views.generate_material_outputs")
    def test_learning_material_can_be_attached_to_outline_node(self, mock_generate):
        response = self.client.post(
            self.url,
            {
                "pdf_file": self.pdf_upload(),
                "title": "States of Matter Material",
                "outline_node_id": self.node.id,
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        material = LearningMaterial.objects.get(course=self.course)
        self.assertEqual(material.outline_node_id, self.node.id)
        self.assertEqual(material.module_node_id, self.node.id)
        self.assertEqual(response.data["materials"][0]["outline_node"], self.node.id)
        mock_generate.assert_called_once_with(material)

    @patch("lessons.views.generate_material_outputs")
    def test_learning_material_can_be_attached_to_top_level_module(self, mock_generate):
        module = OutlineNode.objects.create(
            course=self.course,
            title="Properties of Matter",
            depth=0,
            order=1,
        )

        response = self.client.post(
            self.url,
            {
                "pdf_file": self.pdf_upload(),
                "title": "Module Material",
                "module_node_id": module.id,
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        material = LearningMaterial.objects.get(course=self.course, title="Module Material")
        self.assertEqual(material.module_node_id, module.id)
        self.assertEqual(response.data["materials"][0]["module_node"], module.id)
        mock_generate.assert_called_once_with(material)

    @patch("lessons.views.generate_material_outputs")
    def test_learning_material_rejects_child_as_module_node(self, mock_generate):
        module = OutlineNode.objects.create(
            course=self.course,
            title="Module",
            depth=0,
            order=1,
        )
        child = OutlineNode.objects.create(
            course=self.course,
            parent=module,
            title="Child Lesson",
            depth=1,
            order=0,
        )

        response = self.client.post(
            self.url,
            {
                "pdf_file": self.pdf_upload(),
                "title": "Invalid Module Material",
                "module_node_id": child.id,
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(LearningMaterial.objects.filter(course=self.course, title="Invalid Module Material").count(), 0)
        mock_generate.assert_not_called()

    @patch("lessons.views.generate_material_outputs")
    def test_learning_material_rejects_outline_node_from_another_course(self, mock_generate):
        response = self.client.post(
            self.url,
            {
                "pdf_file": self.pdf_upload(),
                "title": "Invalid Material",
                "outline_node_id": self.other_node.id,
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(LearningMaterial.objects.filter(course=self.course).count(), 0)
        mock_generate.assert_not_called()

    @patch("lessons.views.generate_material_outputs")
    def test_learning_material_requires_selected_module_or_topic(self, mock_generate):
        response = self.client.post(
            self.url,
            {
                "pdf_file": self.pdf_upload(),
                "title": "States of Matter Lesson",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Select a module or topic", response.data["detail"])
        self.assertEqual(LearningMaterial.objects.filter(course=self.course).count(), 0)
        mock_generate.assert_not_called()


class PreservedNarrationGenerationTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Preservation Course")
        self.module = OutlineNode.objects.create(course=self.course, title="Matter", depth=0, order=0)
        self.material = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.module,
            module_node=self.module,
            title="Matter PDF",
            pdf_file=SimpleUploadedFile("matter.pdf", b"%PDF-1.4", content_type="application/pdf"),
        )

    def test_teacher_text_is_preserved_in_narration_script(self):
        paragraph = "Matter is anything that has mass and occupies space."

        narration = build_teacher_text_narration(paragraph)

        self.assertEqual(narration[0]["content"], paragraph)
        self.assertEqual(narration[0]["source"], "pdf_exact_text")

    def test_learning_objects_copy_exact_teacher_text(self):
        paragraph = "A solid has a definite shape and a definite volume."
        narration = build_teacher_text_narration(paragraph)

        objects = build_learning_objects_from_narration(narration)

        self.assertEqual(objects[0]["content"], paragraph)
        self.assertEqual(objects[0]["source_excerpt"], paragraph)
        self.assertEqual(objects[0]["source"], "pdf_exact_text")

    def test_fallback_learning_objects_copy_preserved_pdf_paragraphs(self):
        text = "Matter is anything that has mass and occupies space.\n\nA solid has a definite shape."

        objects = build_fallback_learning_objects_from_text(text)

        self.assertEqual(len(objects), 2)
        self.assertEqual(objects[0]["content"], "Matter is anything that has mass and occupies space.")
        self.assertEqual(objects[1]["content"], "A solid has a definite shape.")

    def test_fallback_learning_objects_do_not_inject_sample_science_terms(self):
        text = "The water cycle moves water through evaporation, condensation, and precipitation."

        objects = build_fallback_learning_objects_from_text(text)

        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0]["content"], text)
        self.assertNotIn("Solid", objects[0]["content"])
        self.assertNotIn("Liquid", objects[0]["content"])
        self.assertNotIn("Gas", objects[0]["content"])

    @patch("lessons.services.instructional_content_classifier.get_llm_client")
    def test_classifier_includes_lesson_content_and_excludes_non_student_blocks(self, mock_client_factory):
        mock_client_factory.return_value.generate_text.return_value = {
            "text": json.dumps(
                {
                    "blocks": [
                        {
                            "block_id": 1,
                            "category": "lesson_content",
                            "include_in_narration": True,
                            "confidence": 0.98,
                            "reason": "Explains matter.",
                        },
                        {
                            "block_id": 2,
                            "category": "teacher_note",
                            "include_in_narration": False,
                            "confidence": 0.95,
                            "reason": "Teacher-facing note.",
                        },
                        {
                            "block_id": 3,
                            "category": "concept_metadata",
                            "include_in_narration": False,
                            "confidence": 0.95,
                            "reason": "Metadata heading.",
                        },
                        {
                            "block_id": 4,
                            "category": "concept_metadata",
                            "include_in_narration": False,
                            "confidence": 0.95,
                            "reason": "Metadata label.",
                        },
                        {
                            "block_id": 6,
                            "category": "concept_metadata",
                            "include_in_narration": False,
                            "confidence": 0.95,
                            "reason": "Metadata table header.",
                        }
                    ]
                }
            )
        }
        blocks = [
            {"block_id": 1, "page": 1, "text": "Matter is anything that has mass and occupies space."},
            {"block_id": 2, "page": 1, "text": "Teacher review note: The local LLM may extract more or fewer concepts."},
            {"block_id": 3, "page": 1, "text": "Key Concepts for Extraction"},
            {"block_id": 4, "page": 1, "text": "Why it matters"},
            {"block_id": 5, "page": 2, "text": "Why does water take the shape of a glass?"},
            {"block_id": 6, "page": 2, "text": "Concept Student-friendly meaning Why it matters"},
        ]

        classified = classify_instructional_blocks(blocks)
        by_id = {item["block_id"]: item for item in classified}

        self.assertTrue(by_id[1]["include_in_narration"])
        self.assertEqual(by_id[1]["text"], blocks[0]["text"])
        self.assertEqual(by_id[2]["category"], "teacher_note")
        self.assertEqual(by_id[3]["category"], "document_metadata")
        self.assertEqual(by_id[4]["category"], "document_metadata")
        self.assertEqual(by_id[5]["category"], "assessment")
        self.assertEqual(by_id[6]["category"], "concept_metadata")
        self.assertFalse(by_id[5]["include_in_narration"])

    @patch("lessons.services.instructional_content_classifier.get_llm_client")
    def test_classifier_uses_llm_for_objectives_and_keeps_structural_headings_out(self, mock_client_factory):
        mock_client_factory.return_value.generate_text.return_value = {
            "text": json.dumps(
                {
                    "blocks": [
                        {
                            "block_id": 2,
                            "category": "learning_objective",
                            "include_in_narration": False,
                            "confidence": 0.94,
                            "reason": "Objective-style learner outcome.",
                        },
                        {
                            "block_id": 4,
                            "category": "lesson_content",
                            "include_in_narration": True,
                            "confidence": 0.98,
                            "reason": "Explains matter.",
                        }
                    ]
                }
            )
        }
        blocks = [
            {"block_id": 1, "page": 1, "text": "Course Packet Page 1"},
            {"block_id": 2, "page": 1, "text": "Define matter as anything that has mass and occupies space."},
            {"block_id": 3, "page": 1, "text": "1. Matter"},
            {
                "block_id": 4,
                "page": 1,
                "text": (
                    "Matter is anything that has mass and occupies space. A book, water, air, "
                    "a spoon, and a balloon all contain matter."
                ),
            },
        ]

        classified = classify_instructional_blocks(blocks)
        by_id = {item["block_id"]: item for item in classified}

        self.assertEqual(by_id[1]["category"], "document_metadata")
        self.assertEqual(by_id[2]["category"], "learning_objective")
        self.assertEqual(by_id[3]["category"], "document_metadata")
        self.assertEqual(by_id[4]["category"], "lesson_content")
        self.assertFalse(by_id[1]["include_in_narration"])
        self.assertFalse(by_id[2]["include_in_narration"])
        self.assertFalse(by_id[3]["include_in_narration"])
        self.assertTrue(by_id[4]["include_in_narration"])

    def test_section_learning_objects_match_lesson_structure(self):
        classified = [
            {
                "block_id": 1,
                "page": 1,
                "text": "Figure 1. Particle arrangement in solids, liquids, and gases. This image is suitable for testing.",
                "category": "teacher_note",
                "include_in_narration": False,
            },
            {"block_id": 2, "page": 1, "text": "1. Matter", "category": "document_metadata", "include_in_narration": False},
            {
                "block_id": 3,
                "page": 1,
                "text": "Matter is anything that has mass and occupies space.",
                "category": "lesson_content",
                "include_in_narration": True,
            },
            {"block_id": 4, "page": 1, "text": "2. Solid", "category": "document_metadata", "include_in_narration": False},
            {
                "block_id": 5,
                "page": 1,
                "text": "A solid has a definite shape and a definite volume.",
                "category": "lesson_content",
                "include_in_narration": True,
            },
            {
                "block_id": 6,
                "page": 1,
                "text": "Teacher review note: verify concepts.",
                "category": "teacher_note",
                "include_in_narration": False,
            },
        ]
        image_descriptions = [
            {
                "page_number": 1,
                "index": 0,
                "description": "The diagram compares particles in a solid, liquid, and gas for BVI learners.",
            }
        ]

        objects = build_section_learning_objects(classified, image_descriptions)
        narration = build_narration_script_from_learning_objects(objects)

        self.assertEqual([item["title"] for item in objects], ["Particle arrangement in solids, liquids, and gases", "Matter", "Solid"])
        self.assertEqual(objects[0]["type"], "image_description")
        self.assertEqual(objects[1]["content"], "Matter is anything that has mass and occupies space.")
        self.assertEqual(objects[2]["content"], "A solid has a definite shape and a definite volume.")
        self.assertNotIn("Teacher review note", "\n".join(item["content"] for item in narration))
        self.assertNotIn("1. Matter", "\n".join(item["content"] for item in narration))

    def test_sparse_subtopic_headings_become_learning_objects(self):
        classified = [
            {"block_id": 1, "page": 1, "text": "1.1 States of Matter", "category": "lesson_content", "include_in_narration": True},
            {"block_id": 2, "page": 1, "text": "1.2 Changes in Matter", "category": "lesson_content", "include_in_narration": True},
        ]

        objects = build_section_learning_objects(classified, [])

        self.assertEqual([item["title"] for item in objects], ["States of Matter", "Changes in Matter"])
        self.assertEqual(objects[0]["content"], "States of Matter")
        self.assertEqual(objects[1]["content"], "Changes in Matter")

    def test_instructional_table_text_is_kept_as_learning_object_content(self):
        classified = [
            {"block_id": 1, "page": 1, "text": "2. Properties of Matter", "category": "lesson_content", "include_in_narration": True},
            {
                "block_id": 2,
                "page": 1,
                "text": "State Description Example Solid has fixed shape and volume ice Liquid has fixed volume water Gas fills container air",
                "category": "concept_metadata",
                "include_in_narration": False,
                "line_count": 3,
            },
        ]

        objects = build_section_learning_objects(classified, [])

        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0]["title"], "Properties of Matter")
        self.assertIn("Solid has fixed shape", objects[0]["content"])

    def test_numbered_subtopic_headings_are_not_classified_as_metadata(self):
        classified = classify_instructional_blocks(
            [{"block_id": 1, "page": 1, "text": "1.1 States of Matter", "line_count": 1}]
        )

        self.assertEqual(classified[0]["category"], "lesson_content")

    @patch("lessons.services.instructional_content_classifier.get_llm_client")
    def test_llm_classifies_objectives_and_setup_notes_without_keyword_rules(self, mock_client_factory):
        mock_client_factory.return_value.generate_text.return_value = {
            "text": json.dumps(
                {
                    "blocks": [
                        {
                            "block_id": 1,
                            "category": "learning_objective",
                            "include_in_narration": False,
                            "confidence": 0.94,
                            "reason": "Learner outcome.",
                        },
                        {
                            "block_id": 2,
                            "category": "concept_metadata",
                            "include_in_narration": False,
                            "confidence": 0.91,
                            "reason": "Setup note for topic sequencing.",
                        },
                        {
                            "block_id": 3,
                            "category": "document_metadata",
                            "include_in_narration": False,
                            "confidence": 0.88,
                            "reason": "Document title.",
                        },
                    ]
                }
            )
        }
        classified = classify_instructional_blocks(
            [
                {
                    "block_id": 1,
                    "page": 1,
                    "text": "Describe the main idea in your own words.",
                    "line_count": 1,
                },
                {
                    "block_id": 2,
                    "page": 1,
                    "text": "Before this topic, learners should already understand the earlier lesson.",
                    "line_count": 1,
                },
                {
                    "block_id": 3,
                    "page": 1,
                    "text": "Introductory Reading Packet",
                    "line_count": 1,
                },
            ]
        )
        by_id = {item["block_id"]: item for item in classified}

        self.assertEqual(by_id[1]["category"], "learning_objective")
        self.assertFalse(by_id[1]["include_in_narration"])
        self.assertEqual(by_id[2]["category"], "concept_metadata")
        self.assertEqual(by_id[3]["category"], "document_metadata")

    def test_non_instructional_front_matter_does_not_become_learning_objects(self):
        classified = [
            {
                "block_id": 1,
                "page": 1,
                "text": "MAVIA sample learning material Page 1",
                "category": "document_metadata",
                "include_in_narration": False,
            },
            {
                "block_id": 2,
                "page": 1,
                "text": "Physical and Chemical Properties of Matter",
                "category": "lesson_content",
                "include_in_narration": True,
            },
            {
                "block_id": 3,
                "page": 1,
                "text": "Physical and Chemical Properties of Matter",
                "category": "decorative_or_noise",
                "include_in_narration": False,
            },
            {
                "block_id": 4,
                "page": 1,
                "text": "Useful and Harmful Materials - Elementary Science Learning Material",
                "category": "lesson_content",
                "include_in_narration": True,
            },
            {
                "block_id": 5,
                "page": 1,
                "text": "Prerequisite connection: Before studying useful and harmful materials, learners should understand observable properties.",
                "category": "concept_metadata",
                "include_in_narration": False,
            },
            {
                "block_id": 6,
                "page": 1,
                "text": "Learning Objectives",
                "category": "learning_objective",
                "include_in_narration": False,
            },
            {
                "block_id": 7,
                "page": 1,
                "text": "Describe common physical properties of matter. Explain how physical changes differ from chemical changes.",
                "category": "learning_objective",
                "include_in_narration": False,
            },
            {
                "block_id": 8,
                "page": 1,
                "text": "What Are Properties of Matter?",
                "category": "lesson_content",
                "include_in_narration": True,
            },
            {
                "block_id": 9,
                "page": 1,
                "text": "Matter is anything that has mass and occupies space. Every material has properties that can be observed or measured.",
                "category": "lesson_content",
                "include_in_narration": True,
            },
            {
                "block_id": 10,
                "page": 1,
                "text": "Physical Properties",
                "category": "lesson_content",
                "include_in_narration": True,
            },
            {
                "block_id": 11,
                "page": 1,
                "text": "Physical properties can be observed or measured without changing the identity of the substance.",
                "category": "lesson_content",
                "include_in_narration": True,
            },
            {
                "block_id": 12,
                "page": 1,
                "text": "Physical property",
                "category": "lesson_content",
                "include_in_narration": True,
            },
            {
                "block_id": 13,
                "page": 1,
                "text": "Physical property",
                "category": "decorative_or_noise",
                "include_in_narration": False,
            },
        ]

        objects = build_section_learning_objects(classified, [])

        self.assertEqual([item["title"] for item in objects], ["What Are Properties of Matter?", "Physical Properties"])
        self.assertNotIn("MAVIA sample learning material", "\n".join(item["content"] for item in objects))
        self.assertNotIn("Learning Objectives", "\n".join(item["title"] for item in objects))
        self.assertNotIn("Prerequisite connection", "\n".join(item["content"] for item in objects))

    @patch("lessons.services.content_generator.get_llm_client")
    def test_learning_object_review_keeps_only_model_approved_items(self, mock_client_factory):
        mock_client_factory.return_value.generate_text.return_value = {
            "text": json.dumps(
                {
                    "items": [
                        {"index": 0, "keep": False, "reason": "Cover label."},
                        {"index": 1, "keep": True, "reason": "Explains a learner-facing idea."},
                        {"index": 2, "keep": False, "reason": "Outcome list."},
                    ]
                }
            )
        }
        learning_objects = [
            {
                "order": 0,
                "title": "Packet Page 1",
                "type": "lesson_content",
                "content": "Packet Page 1",
            },
            {
                "order": 1,
                "title": "Main Idea",
                "type": "lesson_content",
                "content": "A main idea tells what a paragraph is mostly about.",
            },
            {
                "order": 2,
                "title": "Goals",
                "type": "lesson_content",
                "content": "List three things you should be able to do.",
            },
        ]

        reviewed = review_learning_objects_for_bvi_learners(learning_objects)

        self.assertEqual([item["title"] for item in reviewed], ["Main Idea"])
        self.assertEqual(reviewed[0]["order"], 0)

    @patch("lessons.services.instructional_content_classifier.get_llm_client")
    def test_invalid_llm_classification_json_falls_back_safely(self, mock_client_factory):
        mock_client_factory.return_value.generate_text.return_value = {"text": "not json"}
        text = "A solid has a definite shape and a definite volume."

        classified = classify_instructional_blocks([{"block_id": 1, "page": 1, "text": text}])

        self.assertEqual(classified[0]["category"], "lesson_content")
        self.assertEqual(classified[0]["text"], text)
        self.assertTrue(classified[0]["include_in_narration"])

    def test_playlist_references_narration_items(self):
        narration = build_teacher_text_narration("Matter is anything that has mass and occupies space.")

        playlist = build_lesson_playlist(narration)

        self.assertEqual(playlist[0]["narration_item_order"], narration[0]["order"])
        self.assertEqual(playlist[0]["type"], "teacher_text")

    @patch("lessons.services.audio_generator.synthesize_text_to_wav")
    def test_audio_playlist_generation_updates_material_json(self, mock_synthesize):
        material = LearningMaterial.objects.create(
            course=self.course,
            outline_node=self.module,
            module_node=self.module,
            title="Matter",
            pdf_file=SimpleUploadedFile("matter.pdf", b"%PDF-1.4"),
            generated_json={
                "narration_script": [
                    {
                        "order": 1,
                        "type": "lesson_content",
                        "title": "Matter",
                        "content": "Matter is anything that has mass and occupies space.",
                    }
                ],
                "lesson_playlist": [
                    {
                        "order": 0,
                        "title": "Matter",
                        "type": "lesson_content",
                        "narration_item_order": 1,
                    }
                ],
            },
        )

        result = generate_material_audio_playlist(material)

        material.refresh_from_db()
        playlist = material.generated_json["lesson_playlist"]
        self.assertEqual(result["generated_count"], 1)
        self.assertEqual(playlist[0]["audio_status"], "generated")
        self.assertIn("/media/audio_lessons/material_", playlist[0]["audio_url"])
        mock_synthesize.assert_called_once()

    @patch("lessons.services.content_generator.describe_pdf_images")
    @patch("lessons.services.content_generator.extract_meaningful_pdf_images", return_value=[])
    @patch("lessons.services.content_generator.extract_pdf_text_blocks")
    @patch("lessons.services.content_generator.extract_pdf_text")
    @patch("lessons.services.content_generator.get_llm_client")
    @patch("lessons.services.instructional_content_classifier.get_llm_client")
    def test_generate_material_outputs_does_not_use_llm_for_narration_body(
        self,
        mock_classifier_client_factory,
        mock_client_factory,
        mock_extract_text,
        mock_extract_blocks,
        _mock_images,
        mock_descriptions,
    ):
        teacher_text = "Matter is anything that has mass and occupies space."
        mock_extract_text.return_value = teacher_text
        mock_extract_blocks.return_value = [{"block_id": 1, "page": 1, "text": teacher_text}]
        mock_descriptions.return_value = [
            {
                "page_number": 1,
                "index": 0,
                "description": "A simple diagram shows particles close together.",
                "source": "local_vision_model",
            }
        ]
        mock_client_factory.return_value.generate_text.return_value = {
            "text": json.dumps(
                {
                    "lesson_title": "Matter",
                    "concepts": [
                        {
                            "canonical_title": "Matter",
                            "description": "Concept metadata.",
                            "source_excerpt": teacher_text,
                            "confidence": 0.97,
                        }
                    ],
                }
            ),
            "llm_metadata": {
                "provider": "ollama",
                "model": "llama3.2:3b",
                "vision_model": "gemma3:4b",
                "execution": "local",
            },
        }
        mock_classifier_client_factory.return_value.generate_text.return_value = {
            "text": json.dumps(
                {
                    "blocks": [
                        {
                            "block_id": 1,
                            "category": "lesson_content",
                            "include_in_narration": True,
                            "confidence": 0.98,
                            "reason": "Explains matter.",
                        }
                    ]
                }
            )
        }

        generate_material_outputs(self.material)
        self.material.refresh_from_db()

        generated = self.material.generated_json
        self.assertEqual(generated["generated_json_version"], 3)
        self.assertTrue(generated["preservation_metadata"]["teacher_text_preserved"])
        self.assertFalse(generated["preservation_metadata"]["teacher_text_rewritten_by_llm"])
        self.assertEqual(generated["narration_script"][1]["content"], teacher_text)
        self.assertEqual(generated["learning_objects"][1]["content"], teacher_text)
        self.assertEqual(generated["image_descriptions"][0]["source"], "local_vision_model")
        self.assertEqual(generated["narration_script"][0]["type"], "image_description")

    @patch("lessons.services.content_generator.describe_pdf_images", return_value=[])
    @patch("lessons.services.content_generator.extract_meaningful_pdf_images", return_value=[])
    @patch("lessons.services.content_generator.extract_pdf_text_blocks")
    @patch("lessons.services.content_generator.extract_pdf_text")
    @patch("lessons.services.content_generator.get_llm_client")
    @patch("lessons.services.instructional_content_classifier.get_llm_client")
    def test_generate_material_outputs_falls_back_when_classification_has_no_lesson_content(
        self,
        mock_classifier_client_factory,
        mock_client_factory,
        mock_extract_text,
        mock_extract_blocks,
        _mock_images,
        _mock_descriptions,
    ):
        teacher_text = "Matter is anything that has mass and occupies space."
        mock_extract_text.return_value = teacher_text
        mock_extract_blocks.return_value = [{"block_id": 1, "page": 1, "text": teacher_text}]
        mock_client_factory.return_value.generate_text.return_value = {
            "text": json.dumps({"lesson_title": "Matter", "concepts": []}),
            "llm_metadata": {"provider": "ollama"},
        }
        mock_classifier_client_factory.return_value.generate_text.return_value = {
            "text": json.dumps(
                {
                    "blocks": [
                        {
                            "block_id": 1,
                            "category": "learning_objective",
                            "include_in_narration": False,
                            "confidence": 0.91,
                            "reason": "Misclassified as objective.",
                        }
                    ]
                }
            )
        }

        generate_material_outputs(self.material)
        self.material.refresh_from_db()

        self.assertEqual(self.material.status, LearningMaterial.Status.COMPLETED)
        self.assertEqual(self.material.learning_objects.count(), 1)
        self.assertEqual(self.material.learning_objects.first().content, teacher_text)
        self.assertTrue(self.material.generated_json["preservation_metadata"]["learning_object_fallback_used"])

    def test_teacher_override_updates_inclusion_without_llm_call(self):
        self.material.generated_json = {
            "generated_json_version": 3,
            "classified_blocks": [
                {
                    "block_id": 1,
                    "page": 1,
                    "text": "Quick check: What is matter?",
                    "category": "assessment",
                    "include_in_narration": False,
                    "confidence": 0.9,
                    "reason": "Question.",
                }
            ],
            "image_descriptions": [],
        }
        self.material.save(update_fields=["generated_json"])

        with patch("lessons.services.instructional_content_classifier.get_llm_client") as mock_classifier_client:
            apply_classification_override(
                self.material,
                block_id=1,
                category="lesson_content",
                include_in_narration=True,
            )

        self.material.refresh_from_db()
        self.assertFalse(mock_classifier_client.called)
        self.assertEqual(self.material.generated_json["narration_script"][0]["content"], "Quick check: What is matter?")
        self.assertEqual(self.material.learning_objects.count(), 1)


class ModuleConceptExtractionAPITests(APITestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Dynamic Science")
        self.module_one = OutlineNode.objects.create(
            course=self.course,
            title="Dynamic Module One",
            depth=0,
            order=0,
        )
        self.module_two = OutlineNode.objects.create(
            course=self.course,
            title="Dynamic Module Two",
            depth=0,
            order=1,
        )
        self.child = OutlineNode.objects.create(
            course=self.course,
            parent=self.module_one,
            title="Dynamic Child",
            depth=1,
            order=0,
        )
        self.material_one = LearningMaterial.objects.create(
            course=self.course,
            module_node=self.module_one,
            title="Module One PDF",
            extracted_text=(
                "First mention. This PDF teaches particles, mixtures, and material properties.\n"
                "Second mention. This PDF also explains particle arrangements."
            ),
            pdf_file=SimpleUploadedFile("module-one.pdf", b"%PDF-1.4", content_type="application/pdf"),
        )
        self.material_two = LearningMaterial.objects.create(
            course=self.course,
            module_node=self.module_two,
            title="Module Two PDF",
            extracted_text="Other module mention. This PDF also discusses particles in a different module.",
            pdf_file=SimpleUploadedFile("module-two.pdf", b"%PDF-1.4", content_type="application/pdf"),
        )

    def llm_response(self, concepts):
        return {"text": '{"concepts": ' + json.dumps(concepts) + "}"}

    def test_module_list_returns_top_level_nodes_only(self):
        response = self.client.get(reverse("course-modules", kwargs={"pk": self.course.pk}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        module_ids = {item["id"] for item in response.data}
        self.assertEqual(module_ids, {self.module_one.id, self.module_two.id})
        self.assertNotIn(self.child.id, module_ids)

    def test_module_materials_returns_only_selected_module_pdfs(self):
        response = self.client.get(
            reverse(
                "course-module-materials",
                kwargs={"pk": self.course.pk, "module_id": self.module_one.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        material_ids = {item["id"] for item in response.data}
        self.assertEqual(material_ids, {self.material_one.id})
        self.assertEqual(response.data[0]["module_node"], self.module_one.id)

    def test_empty_module_concepts_returns_empty_list(self):
        response = self.client.get(
            reverse(
                "course-module-concepts",
                kwargs={"pk": self.course.pk, "module_id": self.module_one.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["concepts"], [])
        self.assertEqual(response.data["summary"]["total"], 0)

    @patch("lessons.services.concept_extractor.get_llm_client")
    def test_concept_extraction_uses_only_classified_lesson_content(self, mock_llm):
        lesson_text = "Matter is anything that has mass and occupies space."
        self.material_one.generated_json = {
            "generated_json_version": 3,
            "classified_blocks": [
                {
                    "block_id": 1,
                    "page": 1,
                    "text": lesson_text,
                    "category": "lesson_content",
                    "include_in_narration": True,
                },
                {
                    "block_id": 2,
                    "page": 1,
                    "text": "Teacher review note: do not narrate this.",
                    "category": "teacher_note",
                    "include_in_narration": False,
                },
                {
                    "block_id": 3,
                    "page": 1,
                    "text": "Why does water take the shape of a glass?",
                    "category": "assessment",
                    "include_in_narration": False,
                },
            ],
        }
        self.material_one.save(update_fields=["generated_json"])
        mock_llm.return_value.generate_text.return_value = self.llm_response(
            [
                {
                    "title": "Matter",
                    "description": "Definition of matter.",
                    "source_excerpt": lesson_text,
                    "material_id": self.material_one.id,
                    "confidence": 0.95,
                }
            ]
        )

        response = self.client.post(
            reverse("course-extract-module-concepts", kwargs={"pk": self.course.pk, "module_id": self.module_one.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        prompt = mock_llm.return_value.generate_text.call_args.args[0]
        self.assertIn(lesson_text, prompt)
        self.assertNotIn("Teacher review note", prompt)
        self.assertNotIn("Why does water", prompt)

    @patch("lessons.services.concept_extractor.get_llm_client")
    def test_extraction_endpoint_stores_concepts_and_provenance(self, mock_llm):
        mock_llm.return_value.generate_text.return_value = self.llm_response(
            [
                {
                    "title": "Particle Arrangement",
                    "description": "How particles are arranged in materials.",
                    "confidence": 0.91,
                    "section_title": "Section A",
                    "page_number": 2,
                    "source_excerpt": "Particles can be arranged in different ways.",
                    "first_appearance_order": 1,
                    "material_id": self.material_one.id,
                },
                {
                    "title": "Mixture Separation",
                    "description": "Ways to separate mixtures.",
                    "confidence": 0.84,
                    "section_title": "Section B",
                    "page_number": 3,
                    "source_excerpt": "Mixtures can be separated using physical methods.",
                    "first_appearance_order": 2,
                    "material_id": self.material_one.id,
                },
            ]
        )

        response = self.client.post(
            reverse(
                "course-extract-module-concepts",
                kwargs={"pk": self.course.pk, "module_id": self.module_one.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["materials_processed"], 1)
        self.assertEqual(response.data["concepts_created"], 2)
        self.assertEqual(len(response.data["concepts"]), 2)
        concept = ExtractedConcept.objects.get(module_node=self.module_one, canonical_title="Particle Arrangement")
        self.assertEqual(concept.sources.count(), 1)
        source = concept.sources.get()
        self.assertEqual(source.learning_material_id, self.material_one.id)
        self.assertEqual(source.page_number, 2)
        self.assertEqual(source.section_title, "Section A")

    @patch("lessons.services.concept_extractor.get_llm_client")
    def test_deduplication_happens_only_inside_selected_module(self, mock_llm):
        mock_llm.return_value.generate_text.return_value = self.llm_response(
            [
                {
                    "title": "Particle Arrangement",
                    "description": "First source.",
                    "confidence": 0.88,
                    "source_excerpt": "First mention.",
                    "first_appearance_order": 1,
                    "material_id": self.material_one.id,
                },
                {
                    "title": " particle-arrangement ",
                    "description": "Second source.",
                    "confidence": 0.90,
                    "source_excerpt": "Second mention.",
                    "first_appearance_order": 2,
                    "material_id": self.material_one.id,
                },
            ]
        )

        response = self.client.post(
            reverse(
                "course-extract-module-concepts",
                kwargs={"pk": self.course.pk, "module_id": self.module_one.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(ExtractedConcept.objects.filter(module_node=self.module_one).count(), 1)
        concept = ExtractedConcept.objects.get(module_node=self.module_one)
        self.assertEqual(concept.sources.count(), 2)
        self.assertEqual(response.data["duplicates_merged"], 1)

        mock_llm.return_value.generate_text.return_value = self.llm_response(
            [
                {
                    "title": "Particle Arrangement",
                    "description": "Same title but different module.",
                    "confidence": 0.86,
                    "source_excerpt": "Other module mention.",
                    "first_appearance_order": 1,
                    "material_id": self.material_two.id,
                }
            ]
        )
        second_response = self.client.post(
            reverse(
                "course-extract-module-concepts",
                kwargs={"pk": self.course.pk, "module_id": self.module_two.pk},
            )
        )

        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(ExtractedConcept.objects.filter(canonical_title__iexact="Particle Arrangement").count(), 2)
        self.assertEqual(ExtractedConcept.objects.filter(module_node=self.module_two).count(), 1)

    def test_module_without_pdfs_returns_400(self):
        empty_module = OutlineNode.objects.create(
            course=self.course,
            title="Empty Dynamic Module",
            depth=0,
            order=2,
        )

        response = self.client.post(
            reverse(
                "course-extract-module-concepts",
                kwargs={"pk": self.course.pk, "module_id": empty_module.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Upload at least one PDF material", response.data["detail"])


class ModuleConceptReviewAPITests(APITestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Dynamic Review Course")
        self.module_one = OutlineNode.objects.create(
            course=self.course,
            title="Dynamic Review Module One",
            depth=0,
            order=0,
        )
        self.module_two = OutlineNode.objects.create(
            course=self.course,
            title="Dynamic Review Module Two",
            depth=0,
            order=1,
        )
        self.material = LearningMaterial.objects.create(
            course=self.course,
            module_node=self.module_one,
            title="Dynamic Source PDF",
            extracted_text=(
                "Existing source.\n"
                "New source.\n"
                "Rejected new source.\n"
                "Manual source.\n"
                "Pending source."
            ),
            pdf_file=SimpleUploadedFile("dynamic-source.pdf", b"%PDF-1.4", content_type="application/pdf"),
        )

    def concept_url(self, module, concept):
        return reverse(
            "course-module-concept-detail",
            kwargs={"pk": self.course.pk, "module_id": module.pk, "concept_id": concept.pk},
        )

    def create_url(self, module):
        return reverse("course-module-concepts", kwargs={"pk": self.course.pk, "module_id": module.pk})

    def create_concept(self, title, module=None, status_value=None, is_manual=False, order=0):
        return ExtractedConcept.objects.create(
            course=self.course,
            module_node=module or self.module_one,
            canonical_title=title,
            description=f"{title} description.",
            order=order,
            confidence=0.80,
            validation_status=status_value or ExtractedConcept.ValidationStatus.PENDING,
            is_manual=is_manual,
        )

    def llm_response(self, concepts):
        return {"text": '{"concepts": ' + json.dumps(concepts) + "}"}

    def test_manual_concept_creation_stores_approved_concept_in_selected_module(self):
        response = self.client.post(
            self.create_url(self.module_one),
            {
                "canonical_title": "Teacher Added Concept",
                "description": "Teacher-provided description.",
                "order": 5,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        concept = ExtractedConcept.objects.get(canonical_title="Teacher Added Concept")
        self.assertEqual(concept.module_node_id, self.module_one.id)
        self.assertEqual(concept.validation_status, ExtractedConcept.ValidationStatus.APPROVED)
        self.assertTrue(concept.is_manual)
        self.assertEqual(concept.normalized_title, "teacher added concept")

    def test_same_title_blocked_inside_same_module_but_allowed_in_another_module(self):
        self.create_concept("Repeatable Concept", module=self.module_one)

        duplicate_response = self.client.post(
            self.create_url(self.module_one),
            {"canonical_title": " repeatable-concept "},
            format="json",
        )
        other_module_response = self.client.post(
            self.create_url(self.module_two),
            {"canonical_title": "Repeatable Concept"},
            format="json",
        )

        self.assertEqual(duplicate_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(other_module_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ExtractedConcept.objects.filter(canonical_title__iexact="Repeatable Concept").count(), 2)

    def test_concept_title_edit_updates_normalized_title_and_preserves_sources(self):
        concept = self.create_concept("Original Concept")
        source = ConceptSource.objects.create(
            concept=concept,
            learning_material=self.material,
            page_number=1,
            section_title="Original Section",
            source_excerpt="Original excerpt.",
            first_appearance_order=1,
        )

        response = self.client.patch(
            self.concept_url(self.module_one, concept),
            {"canonical_title": "Updated Concept Title", "description": "Updated description."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        concept.refresh_from_db()
        self.assertEqual(concept.normalized_title, "updated concept title")
        self.assertEqual(concept.sources.count(), 1)
        self.assertTrue(ConceptSource.objects.filter(pk=source.pk).exists())

    def test_concept_deletion_affects_only_selected_module(self):
        concept = self.create_concept("Delete Me", module=self.module_one)
        other_concept = self.create_concept("Keep Me", module=self.module_two)

        cross_response = self.client.delete(self.concept_url(self.module_one, other_concept))
        response = self.client.delete(self.concept_url(self.module_one, concept))

        self.assertEqual(cross_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(ExtractedConcept.objects.filter(pk=concept.pk).exists())
        self.assertTrue(ExtractedConcept.objects.filter(pk=other_concept.pk).exists())

    def test_approve_and_reject_one_concept(self):
        pending = self.create_concept("Pending Concept")
        rejected = self.create_concept("Rejectable Concept")

        approve_response = self.client.post(
            reverse(
                "course-approve-module-concept",
                kwargs={"pk": self.course.pk, "module_id": self.module_one.pk, "concept_id": pending.pk},
            )
        )
        reject_response = self.client.post(
            reverse(
                "course-reject-module-concept",
                kwargs={"pk": self.course.pk, "module_id": self.module_one.pk, "concept_id": rejected.pk},
            )
        )

        self.assertEqual(approve_response.status_code, status.HTTP_200_OK)
        self.assertEqual(reject_response.status_code, status.HTTP_200_OK)
        pending.refresh_from_db()
        rejected.refresh_from_db()
        self.assertEqual(pending.validation_status, ExtractedConcept.ValidationStatus.APPROVED)
        self.assertEqual(rejected.validation_status, ExtractedConcept.ValidationStatus.REJECTED)

    def test_approve_all_changes_pending_only_inside_selected_module(self):
        pending = self.create_concept("Pending One", status_value=ExtractedConcept.ValidationStatus.PENDING)
        approved = self.create_concept("Already Approved", status_value=ExtractedConcept.ValidationStatus.APPROVED)
        rejected = self.create_concept("Rejected One", status_value=ExtractedConcept.ValidationStatus.REJECTED)
        other_pending = self.create_concept("Other Pending", module=self.module_two)

        response = self.client.post(
            reverse("course-approve-module-concepts", kwargs={"pk": self.course.pk, "module_id": self.module_one.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["approved_count"], 1)
        self.assertEqual(response.data["already_approved_count"], 1)
        self.assertEqual(response.data["rejected_count"], 1)
        pending.refresh_from_db()
        approved.refresh_from_db()
        rejected.refresh_from_db()
        other_pending.refresh_from_db()
        self.assertEqual(pending.validation_status, ExtractedConcept.ValidationStatus.APPROVED)
        self.assertEqual(approved.validation_status, ExtractedConcept.ValidationStatus.APPROVED)
        self.assertEqual(rejected.validation_status, ExtractedConcept.ValidationStatus.REJECTED)
        self.assertEqual(other_pending.validation_status, ExtractedConcept.ValidationStatus.PENDING)

    def test_detail_endpoint_returns_full_provenance_fields(self):
        concept = self.create_concept("Detailed Concept")
        ConceptSource.objects.create(
            concept=concept,
            learning_material=self.material,
            page_number=4,
            section_title="Dynamic Section",
            source_excerpt="Dynamic excerpt.",
            first_appearance_order=7,
        )

        response = self.client.get(self.concept_url(self.module_one, concept))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data.keys()),
            {
                "id",
                "course",
                "module_node",
                "canonical_title",
                "normalized_title",
                "description",
                "order",
                "confidence",
                "validation_status",
                "is_manual",
                "created_at",
                "updated_at",
                "sources",
            },
        )
        source = response.data["sources"][0]
        self.assertEqual(
            set(source.keys()),
            {
                "id",
                "learning_material",
                "material_title",
                "material_filename",
                "page_number",
                "section_title",
                "source_excerpt",
                "first_appearance_order",
            },
        )

    def test_readiness_summary_rules(self):
        pending = self.create_concept("Pending Readiness", status_value=ExtractedConcept.ValidationStatus.PENDING)
        approved_one = self.create_concept("Approved One", status_value=ExtractedConcept.ValidationStatus.APPROVED)

        response_with_pending = self.client.get(self.create_url(self.module_one))
        self.assertFalse(response_with_pending.data["summary"]["ready_for_dag"])

        pending.validation_status = ExtractedConcept.ValidationStatus.REJECTED
        pending.save(update_fields=["validation_status"])
        response_with_one_approved = self.client.get(self.create_url(self.module_one))
        self.assertFalse(response_with_one_approved.data["summary"]["ready_for_dag"])

        self.create_concept("Approved Two", status_value=ExtractedConcept.ValidationStatus.APPROVED, order=2)
        response_ready = self.client.get(self.create_url(self.module_one))
        self.assertTrue(response_ready.data["summary"]["ready_for_dag"])
        self.assertEqual(response_ready.data["summary"]["approved"], 2)
        self.assertEqual(response_ready.data["summary"]["pending"], 0)

    @patch("lessons.services.concept_extractor.get_llm_client")
    def test_reextraction_preserves_reviewed_concepts_and_adds_missing_sources(self, mock_llm):
        approved = self.create_concept(
            "Reviewed Concept",
            status_value=ExtractedConcept.ValidationStatus.APPROVED,
            is_manual=False,
        )
        approved.description = "Teacher edited description."
        approved.save(update_fields=["description"])
        rejected = self.create_concept(
            "Rejected Concept",
            status_value=ExtractedConcept.ValidationStatus.REJECTED,
        )
        manual = self.create_concept(
            "Manual Concept",
            status_value=ExtractedConcept.ValidationStatus.APPROVED,
            is_manual=True,
        )
        pending = self.create_concept(
            "Pending Concept",
            status_value=ExtractedConcept.ValidationStatus.PENDING,
        )
        ConceptSource.objects.create(
            concept=approved,
            learning_material=self.material,
            source_excerpt="Existing source.",
            first_appearance_order=1,
        )
        mock_llm.return_value.generate_text.return_value = self.llm_response(
            [
                {
                    "title": "Reviewed Concept",
                    "description": "LLM should not overwrite approved.",
                    "confidence": 0.10,
                    "source_excerpt": "Existing source.",
                    "first_appearance_order": 1,
                    "material_id": self.material.id,
                },
                {
                    "title": "Reviewed Concept",
                    "description": "LLM should not overwrite approved.",
                    "confidence": 0.10,
                    "source_excerpt": "New source.",
                    "first_appearance_order": 2,
                    "material_id": self.material.id,
                },
                {
                    "title": "Rejected Concept",
                    "description": "LLM should not overwrite rejected.",
                    "confidence": 0.99,
                    "source_excerpt": "Rejected new source.",
                    "first_appearance_order": 3,
                    "material_id": self.material.id,
                },
                {
                    "title": "Manual Concept",
                    "description": "LLM should not overwrite manual.",
                    "confidence": 0.99,
                    "source_excerpt": "Manual source.",
                    "first_appearance_order": 4,
                    "material_id": self.material.id,
                },
                {
                    "title": "Pending Concept",
                    "description": "Pending can be updated.",
                    "confidence": 0.95,
                    "source_excerpt": "Pending source.",
                    "first_appearance_order": 5,
                    "material_id": self.material.id,
                },
            ]
        )

        response = self.client.post(
            reverse(
                "course-extract-module-concepts",
                kwargs={"pk": self.course.pk, "module_id": self.module_one.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        approved.refresh_from_db()
        rejected.refresh_from_db()
        manual.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(approved.description, "Teacher edited description.")
        self.assertEqual(rejected.description, "Rejected Concept description.")
        self.assertEqual(manual.description, "Manual Concept description.")
        self.assertEqual(pending.description, "Pending can be updated.")
        self.assertEqual(approved.sources.count(), 2)
        self.assertEqual(
            ConceptSource.objects.filter(concept=approved, source_excerpt="Existing source.").count(),
            1,
        )

    def test_invalid_module_or_cross_module_concept_access_is_rejected(self):
        concept = self.create_concept("Module One Concept", module=self.module_one)
        child = OutlineNode.objects.create(
            course=self.course,
            parent=self.module_one,
            title="Not A Module",
            depth=1,
            order=0,
        )

        invalid_module_response = self.client.get(
            reverse(
                "course-module-concepts",
                kwargs={"pk": self.course.pk, "module_id": child.pk},
            )
        )
        cross_module_response = self.client.patch(
            self.concept_url(self.module_two, concept),
            {"description": "Should not update."},
            format="json",
        )

        self.assertEqual(invalid_module_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(cross_module_response.status_code, status.HTTP_404_NOT_FOUND)


class CourseLearningObjectMutationTests(APITestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 8 Science")
        self.material = LearningMaterial.objects.create(
            course=self.course,
            title="States of Matter PDF",
            pdf_file=SimpleUploadedFile(
                "states.pdf",
                b"%PDF-1.4 fake pdf content",
                content_type="application/pdf",
            ),
        )
        self.learning_object = LearningObject.objects.create(
            material=self.material,
            kind=LearningObject.Kind.TEXT,
            title="Original Script",
            content="Original generated explanation.",
            order=0,
        )
        self.other_course = CourseGroup.objects.create(title="Other Course")
        self.other_material = LearningMaterial.objects.create(
            course=self.other_course,
            title="Other PDF",
            pdf_file=SimpleUploadedFile(
                "other.pdf",
                b"%PDF-1.4 fake pdf content",
                content_type="application/pdf",
            ),
        )

    def test_teacher_can_create_learning_object_for_material(self):
        response = self.client.post(
            reverse(
                "course-create-learning-object",
                kwargs={"pk": self.course.pk, "material_id": self.material.pk},
            ),
            {
                "kind": "image",
                "title": "Particle Explanation",
                "content": "Explain how particles are arranged in solids, liquids, and gases.",
                "image_prompt": "A labeled particle diagram for three states of matter.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        created = LearningObject.objects.get(title="Particle Explanation")
        self.assertEqual(created.material_id, self.material.id)
        self.assertEqual(created.kind, LearningObject.Kind.TEXT)
        self.assertEqual(created.image_prompt, "")
        response_object = response.data["materials"][0]["learning_objects"][-1]
        self.assertEqual(response_object["title"], "Particle Explanation")
        self.assertNotIn("kind", response_object)
        self.assertNotIn("image_prompt", response_object)

    def test_teacher_can_update_learning_object_script(self):
        response = self.client.patch(
            reverse(
                "course-learning-object-detail",
                kwargs={
                    "pk": self.course.pk,
                    "material_id": self.material.pk,
                    "object_id": self.learning_object.pk,
                },
            ),
            {"title": "Edited Script", "content": "Teacher-reviewed explanation."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.learning_object.refresh_from_db()
        self.assertEqual(self.learning_object.title, "Edited Script")
        self.assertEqual(self.learning_object.content, "Teacher-reviewed explanation.")

    def test_teacher_can_delete_learning_object(self):
        response = self.client.delete(
            reverse(
                "course-learning-object-detail",
                kwargs={
                    "pk": self.course.pk,
                    "material_id": self.material.pk,
                    "object_id": self.learning_object.pk,
                },
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(LearningObject.objects.filter(pk=self.learning_object.pk).exists())

    def test_teacher_can_confirm_learning_objects(self):
        response = self.client.post(
            reverse(
                "course-confirm-learning-objects",
                kwargs={"pk": self.course.pk, "material_id": self.material.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.material.refresh_from_db()
        self.assertTrue(self.material.generated_json["learning_objects_confirmed"])
        self.assertEqual(self.material.generated_json["learning_objects"][0]["title"], "Original Script")

    def test_editing_learning_object_marks_confirmation_stale(self):
        self.material.generated_json = {"learning_objects_confirmed": True}
        self.material.save(update_fields=["generated_json"])

        response = self.client.patch(
            reverse(
                "course-learning-object-detail",
                kwargs={
                    "pk": self.course.pk,
                    "material_id": self.material.pk,
                    "object_id": self.learning_object.pk,
                },
            ),
            {"title": "Edited Script", "content": "Teacher-reviewed explanation."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.material.refresh_from_db()
        self.assertFalse(self.material.generated_json["learning_objects_confirmed"])

    def test_learning_object_routes_do_not_cross_courses(self):
        response = self.client.post(
            reverse(
                "course-create-learning-object",
                kwargs={"pk": self.course.pk, "material_id": self.other_material.pk},
            ),
            {"title": "Invalid", "content": "Should not be created."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(LearningObject.objects.filter(title="Invalid").exists())


class CourseGenerateDAGAPITests(APITestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Grade 8 Science")
        self.matter = OutlineNode.objects.create(
            course=self.course,
            title="Matter",
            depth=0,
            order=0,
        )
        self.states = OutlineNode.objects.create(
            course=self.course,
            title="States of Matter",
            depth=0,
            order=1,
        )
        self.other_course = CourseGroup.objects.create(title="Other Science")
        self.other_source = OutlineNode.objects.create(
            course=self.other_course,
            title="Force",
            depth=0,
            order=0,
        )
        self.other_target = OutlineNode.objects.create(
            course=self.other_course,
            title="Motion",
            depth=0,
            order=1,
        )
        self.url = reverse("course-generate-dag", kwargs={"pk": self.course.pk})

    def summary(self, **overrides):
        data = {
            "pairs_evaluated": 1,
            "edges_created": 1,
            "edges_updated": 0,
            "edges_deleted": 0,
            "edges_skipped_as_siblings": 0,
            "threshold": EDGE_SCORE_THRESHOLD,
        }
        data.update(overrides)
        return data

    def create_generated_edge(self, _course):
        OutlineEdge.objects.update_or_create(
            course=self.course,
            source=self.matter,
            target=self.states,
            defaults={
                "score": 0.86,
                "semantic_similarity": 0.72,
                "outline_order_score": 1.0,
                "explanation": "Matter appears before States of Matter in the outline.",
                "validation_status": OutlineEdge.ValidationStatus.PENDING,
                "is_manual": False,
            },
        )
        return self.summary()

    @patch("lessons.views.generate_prerequisite_edges")
    def test_successful_generation_returns_200_and_payload(self, mock_generate):
        mock_generate.side_effect = self.create_generated_edge

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["message"],
            "Candidate prerequisite edges generated successfully.",
        )
        self.assertIn("summary", response.data)
        self.assertIn("nodes", response.data)
        self.assertIn("edges", response.data)
        self.assertEqual(len(response.data["nodes"]), 2)
        self.assertEqual(len(response.data["edges"]), 1)

    @patch("lessons.views.generate_prerequisite_edges")
    def test_no_outline_nodes_returns_400(self, mock_generate):
        empty_course = CourseGroup.objects.create(title="Empty Course")

        response = self.client.post(reverse("course-generate-dag", kwargs={"pk": empty_course.pk}))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)
        mock_generate.assert_not_called()

    @patch("lessons.views.generate_prerequisite_edges")
    def test_generated_edges_remain_pending(self, mock_generate):
        mock_generate.side_effect = self.create_generated_edge

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["edges"][0]["validation_status"], OutlineEdge.ValidationStatus.PENDING)

    @patch("lessons.views.generate_prerequisite_edges")
    def test_repeated_calls_do_not_create_duplicate_edges(self, mock_generate):
        mock_generate.side_effect = self.create_generated_edge

        first_response = self.client.post(self.url)
        second_response = self.client.post(self.url)

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(OutlineEdge.objects.filter(course=self.course, source=self.matter, target=self.states).count(), 1)

    @patch("lessons.views.generate_prerequisite_edges")
    def test_approved_rejected_and_manual_edges_are_preserved(self, mock_generate):
        approved = OutlineEdge.objects.create(
            course=self.course,
            source=self.matter,
            target=self.states,
            score=0.91,
            semantic_similarity=0.82,
            outline_order_score=1.0,
            validation_status=OutlineEdge.ValidationStatus.APPROVED,
            explanation="Teacher approved.",
        )
        rejected_source = OutlineNode.objects.create(course=self.course, title="Properties", depth=0, order=2)
        rejected = OutlineEdge.objects.create(
            course=self.course,
            source=self.states,
            target=rejected_source,
            score=0.12,
            semantic_similarity=0.20,
            outline_order_score=1.0,
            validation_status=OutlineEdge.ValidationStatus.REJECTED,
            explanation="Teacher rejected.",
        )
        manual_target = OutlineNode.objects.create(course=self.course, title="Applications", depth=0, order=3)
        manual = OutlineEdge.objects.create(
            course=self.course,
            source=self.matter,
            target=manual_target,
            score=0.50,
            semantic_similarity=0.50,
            outline_order_score=1.0,
            is_manual=True,
            explanation="Teacher added manually.",
        )
        mock_generate.return_value = self.summary(edges_created=0)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        approved.refresh_from_db()
        rejected.refresh_from_db()
        manual.refresh_from_db()
        self.assertEqual(approved.validation_status, OutlineEdge.ValidationStatus.APPROVED)
        self.assertEqual(approved.explanation, "Teacher approved.")
        self.assertEqual(rejected.validation_status, OutlineEdge.ValidationStatus.REJECTED)
        self.assertEqual(rejected.explanation, "Teacher rejected.")
        self.assertTrue(manual.is_manual)
        self.assertEqual(manual.explanation, "Teacher added manually.")

    @patch("lessons.views.generate_prerequisite_edges")
    def test_records_from_another_course_are_not_included(self, mock_generate):
        mock_generate.side_effect = self.create_generated_edge
        other_edge = OutlineEdge.objects.create(
            course=self.other_course,
            source=self.other_source,
            target=self.other_target,
            score=0.88,
            semantic_similarity=0.76,
            outline_order_score=1.0,
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        edge_ids = {edge["id"] for edge in response.data["edges"]}
        self.assertNotIn(other_edge.id, edge_ids)

    def test_nonexistent_course_returns_404(self):
        response = self.client.post(reverse("course-generate-dag", kwargs={"pk": 999999}))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("lessons.views.generate_prerequisite_edges")
    def test_service_model_failure_returns_safe_503_response(self, mock_generate):
        mock_generate.side_effect = RuntimeError("model download details")

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(
            response.data["detail"],
            "The semantic similarity model could not be loaded. Please try again after the model is installed or cached.",
        )
        self.assertNotIn("model download details", str(response.data))

    @patch("lessons.views.generate_prerequisite_edges")
    def test_unexpected_service_failure_returns_safe_500_response(self, mock_generate):
        mock_generate.side_effect = ValueError("raw implementation detail")

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(response.data["detail"], "Candidate prerequisite edges could not be generated.")
        self.assertNotIn("raw implementation detail", str(response.data))

    @patch("lessons.views.generate_prerequisite_edges")
    def test_permissions_match_existing_course_actions(self, mock_generate):
        mock_generate.side_effect = self.create_generated_edge

        dag_response = self.client.get(reverse("course-dag", kwargs={"pk": self.course.pk}))
        generate_response = self.client.post(self.url)

        self.assertNotIn(dag_response.status_code, {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN})
        self.assertNotIn(generate_response.status_code, {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN})


class CourseModuleDAGAPITests(APITestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Dynamic Science")
        self.module_one = OutlineNode.objects.create(
            course=self.course,
            title="Dynamic Module Alpha",
            depth=0,
            order=0,
        )
        self.alpha_a = OutlineNode.objects.create(
            course=self.course,
            parent=self.module_one,
            title="Alpha Concept A",
            depth=1,
            order=0,
        )
        self.alpha_b = OutlineNode.objects.create(
            course=self.course,
            parent=self.module_one,
            title="Alpha Concept B",
            depth=1,
            order=1,
        )
        self.alpha_nested = OutlineNode.objects.create(
            course=self.course,
            parent=self.alpha_b,
            title="Alpha Nested Concept",
            depth=2,
            order=2,
        )
        self.module_two = OutlineNode.objects.create(
            course=self.course,
            title="Dynamic Module Beta",
            depth=0,
            order=1,
        )
        self.beta_a = OutlineNode.objects.create(
            course=self.course,
            parent=self.module_two,
            title="Beta Concept A",
            depth=1,
            order=0,
        )
        self.beta_b = OutlineNode.objects.create(
            course=self.course,
            parent=self.module_two,
            title="Beta Concept B",
            depth=1,
            order=1,
        )
        self.module_one_edge = OutlineEdge.objects.create(
            course=self.course,
            source=self.alpha_a,
            target=self.alpha_b,
            score=0.88,
            semantic_similarity=0.76,
            outline_order_score=1.0,
        )
        self.module_two_edge = OutlineEdge.objects.create(
            course=self.course,
            source=self.beta_a,
            target=self.beta_b,
            score=0.86,
            semantic_similarity=0.72,
            outline_order_score=1.0,
        )

    def test_module_one_dag_contains_only_module_one_descendants(self):
        response = self.client.get(
            reverse(
                "course-module-dag",
                kwargs={"pk": self.course.pk, "module_id": self.module_one.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        node_ids = {node["id"] for node in response.data["nodes"]}
        edge_ids = {edge["id"] for edge in response.data["edges"]}
        self.assertEqual(node_ids, {self.alpha_a.id, self.alpha_b.id, self.alpha_nested.id})
        self.assertNotIn(self.module_one.id, node_ids)
        self.assertNotIn(self.module_two.id, node_ids)
        self.assertNotIn(self.beta_a.id, node_ids)
        self.assertEqual(edge_ids, {self.module_one_edge.id})
        self.assertEqual(response.data["module"]["title"], "Dynamic Module Alpha")

    def test_module_two_dag_contains_only_module_two_descendants(self):
        response = self.client.get(
            reverse(
                "course-module-dag",
                kwargs={"pk": self.course.pk, "module_id": self.module_two.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        node_ids = {node["id"] for node in response.data["nodes"]}
        edge_ids = {edge["id"] for edge in response.data["edges"]}
        self.assertEqual(node_ids, {self.beta_a.id, self.beta_b.id})
        self.assertEqual(edge_ids, {self.module_two_edge.id})

    @patch("lessons.views.generate_prerequisite_edges")
    def test_generate_module_dag_calls_service_with_selected_module(self, mock_generate):
        mock_generate.return_value = {
            "pairs_evaluated": 1,
            "edges_created": 0,
            "edges_updated": 0,
            "edges_deleted": 0,
            "threshold": EDGE_SCORE_THRESHOLD,
        }

        response = self.client.post(
            reverse(
                "course-generate-module-dag",
                kwargs={"pk": self.course.pk, "module_id": self.module_one.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_generate.assert_called_once()
        _course_arg, = mock_generate.call_args.args
        self.assertEqual(_course_arg.id, self.course.id)
        self.assertEqual(mock_generate.call_args.kwargs["module_node"].id, self.module_one.id)
        node_ids = {node["id"] for node in response.data["nodes"]}
        self.assertEqual(node_ids, {self.alpha_a.id, self.alpha_b.id, self.alpha_nested.id})

    def test_module_with_fewer_than_two_concepts_cannot_generate(self):
        single_module = OutlineNode.objects.create(
            course=self.course,
            title="Single Dynamic Module",
            depth=0,
            order=2,
        )
        OutlineNode.objects.create(
            course=self.course,
            parent=single_module,
            title="Only Concept",
            depth=1,
            order=0,
        )

        response = self.client.post(
            reverse(
                "course-generate-module-dag",
                kwargs={"pk": self.course.pk, "module_id": single_module.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"],
            "This module needs at least two lesson concepts before a DAG can be generated.",
        )

    def test_child_node_cannot_be_used_as_module_id(self):
        response = self.client.get(
            reverse(
                "course-module-dag",
                kwargs={"pk": self.course.pk, "module_id": self.alpha_a.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class PrerequisiteEdgeGeneratorTests(TestCase):
    def create_node(self, course, title, order, depth=0, parent=None):
        return OutlineNode.objects.create(
            course=course,
            parent=parent,
            title=title,
            order=order,
            depth=depth,
        )

    def similarity_from_map(self, values, default=0.80):
        def _similarity(source, target):
            return values.get((source.title, target.title), default)

        return _similarity

    @patch("lessons.services.edge_generator.compute_semantic_similarity", return_value=0.90)
    def test_nearby_non_sibling_nodes_can_be_compared(self, _similarity):
        course = CourseGroup.objects.create(title="Science")
        section = self.create_node(course, "Matter Unit", 0)
        source = self.create_node(course, "Matter", 1, depth=1, parent=section)
        target = self.create_node(course, "States of Matter", 2, depth=2, parent=source)

        summary = generate_prerequisite_edges(course)

        self.assertTrue(OutlineEdge.objects.filter(source=source, target=target).exists())
        self.assertGreater(summary["pairs_considered_after_filtering"], 0)

    @patch("lessons.services.edge_generator.compute_semantic_similarity", return_value=0.90)
    def test_far_apart_unrelated_nodes_are_not_compared(self, _similarity):
        course = CourseGroup.objects.create(title="Science")
        module = self.create_node(course, "Matter Module", 0)
        first = self.create_node(course, "Matter", 0, depth=1, parent=module)
        for index in range(1, MAX_FORWARD_DISTANCE + 2):
            self.create_node(course, f"Filler {index}", index, depth=1, parent=module)
        far = self.create_node(course, "Distant Topic", MAX_FORWARD_DISTANCE + 2, depth=1, parent=module)

        summary = generate_prerequisite_edges(course)

        self.assertFalse(OutlineEdge.objects.filter(source=first, target=far).exists())
        self.assertGreater(summary["pairs_skipped_by_distance"], 0)

    @patch("lessons.services.edge_generator.compute_semantic_similarity", return_value=0.60)
    def test_parent_to_child_remains_eligible(self, _similarity):
        course = CourseGroup.objects.create(title="Science")
        module = self.create_node(course, "Matter Module", 0)
        parent = self.create_node(course, "Matter", 0, depth=1, parent=module)
        child = self.create_node(course, "States of Matter", 10, depth=2, parent=parent)

        generate_prerequisite_edges(course)

        self.assertTrue(OutlineEdge.objects.filter(source=parent, target=child).exists())

    @patch("lessons.services.edge_generator.compute_semantic_similarity", return_value=0.60)
    def test_ancestor_to_descendant_within_depth_gap_remains_eligible(self, _similarity):
        course = CourseGroup.objects.create(title="Science")
        module = self.create_node(course, "Matter Module", 0)
        root = self.create_node(course, "Matter", 0, depth=1, parent=module)
        middle = self.create_node(course, "States of Matter", 1, depth=2, parent=root)
        descendant = self.create_node(course, "Solid State", 20, depth=3, parent=middle)

        generate_prerequisite_edges(course)

        self.assertTrue(OutlineEdge.objects.filter(source=root, target=descendant).exists())
        self.assertLessEqual(descendant.depth - root.depth, MAX_ANCESTOR_DEPTH_GAP)

    @patch("lessons.services.edge_generator.compute_semantic_similarity", return_value=1.00)
    def test_ancestor_to_descendant_beyond_depth_gap_is_skipped(self, _similarity):
        course = CourseGroup.objects.create(title="Science")
        module = self.create_node(course, "Module", 0)
        root = self.create_node(course, "Root Concept", 0, depth=1, parent=module)
        parent = root
        for depth in range(2, MAX_ANCESTOR_DEPTH_GAP + 3):
            parent = self.create_node(course, f"Level {depth}", depth, depth=depth, parent=parent)

        summary = generate_prerequisite_edges(course)

        self.assertFalse(OutlineEdge.objects.filter(source=root, target=parent).exists())
        self.assertGreater(summary["pairs_skipped_by_distance"], 0)

    @patch("lessons.services.edge_generator.compute_semantic_similarity", return_value=1.00)
    def test_direct_sibling_concepts_inside_same_module_can_be_compared(self, _similarity):
        course = CourseGroup.objects.create(title="Science")
        parent = self.create_node(course, "Living Things", 0)
        animals = self.create_node(course, "Animals", 1, depth=1, parent=parent)
        plants = self.create_node(course, "Plants", 2, depth=1, parent=parent)

        summary = generate_prerequisite_edges(course)

        self.assertTrue(OutlineEdge.objects.filter(source=animals, target=plants).exists())
        self.assertEqual(summary["pairs_skipped_as_siblings"], 0)

    @patch("lessons.services.edge_generator.compute_semantic_similarity", return_value=0.50)
    def test_unrelated_top_level_sections_are_skipped(self, _similarity):
        course = CourseGroup.objects.create(title="Science")
        matter = self.create_node(course, "Matter Module", 0)
        force = self.create_node(course, "Force Module", 1)
        self.create_node(course, "States of Matter", 0, depth=1, parent=matter)
        self.create_node(course, "Motion", 0, depth=1, parent=force)

        summary = generate_prerequisite_edges(course)

        self.assertEqual(OutlineEdge.objects.count(), 0)
        self.assertEqual(summary["pairs_skipped_cross_section"], 1)

    @patch("lessons.services.edge_generator.compute_semantic_similarity")
    def test_high_similarity_cross_module_pair_is_not_eligible(self, mock_similarity):
        course = CourseGroup.objects.create(title="Science")
        module_a = self.create_node(course, "Matter Module", 0)
        module_b = self.create_node(course, "Force Module", 1)
        source = self.create_node(course, "Matter", 0, depth=1, parent=module_a)
        target = self.create_node(course, "Mass and Matter", 0, depth=1, parent=module_b)
        mock_similarity.return_value = 1.00

        generate_prerequisite_edges(course)

        self.assertFalse(OutlineEdge.objects.filter(source=source, target=target).exists())
        mock_similarity.assert_not_called()

    @patch("lessons.services.edge_generator.compute_semantic_similarity")
    def test_no_source_gets_more_than_two_automatic_outgoing_edges(self, mock_similarity):
        course = CourseGroup.objects.create(title="Science")
        module = self.create_node(course, "Matter Module", 0)
        root = self.create_node(course, "Matter", 0, depth=1, parent=module)
        targets = [self.create_node(course, f"Topic {index}", index + 1, depth=1, parent=module) for index in range(4)]
        mock_similarity.side_effect = self.similarity_from_map(
            {
                ("Matter", "Topic 0"): 0.95,
                ("Matter", "Topic 1"): 0.90,
                ("Matter", "Topic 2"): 0.85,
                ("Matter", "Topic 3"): 0.80,
            },
            default=0.30,
        )

        summary = generate_prerequisite_edges(course)

        self.assertLessEqual(OutlineEdge.objects.filter(source=root).count(), MAX_OUTGOING_EDGES_PER_NODE)
        kept_targets = set(OutlineEdge.objects.filter(source=root).values_list("target__title", flat=True))
        self.assertEqual(kept_targets, {targets[0].title, targets[1].title})
        self.assertGreater(summary["candidates_removed_by_outgoing_limit"], 0)

    @patch("lessons.services.edge_generator.compute_semantic_similarity")
    def test_no_target_gets_more_than_three_automatic_incoming_edges(self, mock_similarity):
        course = CourseGroup.objects.create(title="Science")
        module = self.create_node(course, "Matter Module", 0)
        root = self.create_node(course, "Source 0", 0, depth=1, parent=module)
        sources = [root] + [
            self.create_node(course, f"Source {index}", index, depth=1, parent=module)
            for index in range(1, 4)
        ]
        target = self.create_node(course, "Applications", 10, depth=2, parent=root)
        mock_similarity.side_effect = self.similarity_from_map(
            {
                ("Source 0", "Applications"): 0.95,
                ("Source 1", "Applications"): 0.90,
                ("Source 2", "Applications"): 0.85,
                ("Source 3", "Applications"): 0.80,
            },
            default=0.10,
        )

        summary = generate_prerequisite_edges(course)

        self.assertLessEqual(OutlineEdge.objects.filter(target=target).count(), MAX_INCOMING_EDGES_PER_NODE)
        kept_sources = set(OutlineEdge.objects.filter(target=target).values_list("source__title", flat=True))
        self.assertEqual(kept_sources, {sources[0].title, sources[1].title, sources[2].title})
        self.assertGreater(summary["candidates_removed_by_incoming_limit"], 0)

    @patch("lessons.services.edge_generator.compute_semantic_similarity")
    def test_highest_scoring_candidates_are_retained(self, mock_similarity):
        course = CourseGroup.objects.create(title="Science")
        module = self.create_node(course, "Matter Module", 0)
        root = self.create_node(course, "Matter", 0, depth=1, parent=module)
        weak = self.create_node(course, "Weak Candidate", 1, depth=1, parent=module)
        strong = self.create_node(course, "Strong Candidate", 2, depth=2, parent=weak)
        stronger = self.create_node(course, "Stronger Candidate", 3, depth=2, parent=weak)
        mock_similarity.side_effect = self.similarity_from_map(
            {
                ("Matter", "Weak Candidate"): 0.60,
                ("Matter", "Strong Candidate"): 0.90,
                ("Matter", "Stronger Candidate"): 0.95,
            },
            default=0.20,
        )

        generate_prerequisite_edges(course)

        kept = set(OutlineEdge.objects.filter(source=root).values_list("target__title", flat=True))
        self.assertIn(strong.title, kept)
        self.assertIn(stronger.title, kept)
        self.assertNotIn(weak.title, kept)

    @patch("lessons.services.edge_generator.compute_semantic_similarity", return_value=0.20)
    def test_stale_pending_automatic_edge_is_removed_after_regeneration(self, _similarity):
        course = CourseGroup.objects.create(title="Science")
        module = self.create_node(course, "Matter Module", 0)
        source = self.create_node(course, "Matter", 0, depth=1, parent=module)
        target = self.create_node(course, "Unrelated Topic", 1, depth=1, parent=module)
        stale_edge = OutlineEdge.objects.create(
            course=course,
            source=source,
            target=target,
            score=0.80,
            semantic_similarity=0.60,
            outline_order_score=1.0,
        )

        summary = generate_prerequisite_edges(course)

        self.assertFalse(OutlineEdge.objects.filter(id=stale_edge.id).exists())
        self.assertEqual(summary["edges_deleted"], 1)

    @patch("lessons.services.edge_generator.compute_semantic_similarity", return_value=0.20)
    def test_module_regeneration_does_not_delete_other_module_pending_edges(self, _similarity):
        course = CourseGroup.objects.create(title="Science")
        module_one = self.create_node(course, "Module One", 0)
        module_one_source = self.create_node(course, "One A", 0, depth=1, parent=module_one)
        module_one_target = self.create_node(course, "One B", 1, depth=1, parent=module_one)
        module_two = self.create_node(course, "Module Two", 1)
        module_two_source = self.create_node(course, "Two A", 0, depth=1, parent=module_two)
        module_two_target = self.create_node(course, "Two B", 1, depth=1, parent=module_two)
        stale_module_one_edge = OutlineEdge.objects.create(
            course=course,
            source=module_one_source,
            target=module_one_target,
            score=0.80,
            semantic_similarity=0.60,
            outline_order_score=1.0,
        )
        module_two_edge = OutlineEdge.objects.create(
            course=course,
            source=module_two_source,
            target=module_two_target,
            score=0.82,
            semantic_similarity=0.64,
            outline_order_score=1.0,
        )

        summary = generate_prerequisite_edges(course, module_node=module_one)

        self.assertEqual(summary["edges_deleted"], 1)
        self.assertFalse(OutlineEdge.objects.filter(id=stale_module_one_edge.id).exists())
        self.assertTrue(OutlineEdge.objects.filter(id=module_two_edge.id).exists())

    @patch("lessons.services.edge_generator.compute_semantic_similarity", return_value=0.90)
    def test_approved_rejected_and_manual_edges_remain_untouched(self, _similarity):
        course = CourseGroup.objects.create(title="Science")
        module = self.create_node(course, "Matter Module", 0)
        source = self.create_node(course, "Matter", 0, depth=1, parent=module)
        approved_target = self.create_node(course, "States", 1, depth=1, parent=module)
        rejected_target = self.create_node(course, "Changes", 2, depth=1, parent=module)
        manual_target = self.create_node(course, "Applications", 3, depth=1, parent=module)
        approved = OutlineEdge.objects.create(
            course=course,
            source=source,
            target=approved_target,
            score=0.42,
            validation_status=OutlineEdge.ValidationStatus.APPROVED,
            explanation="Approved.",
        )
        rejected = OutlineEdge.objects.create(
            course=course,
            source=source,
            target=rejected_target,
            score=0.42,
            validation_status=OutlineEdge.ValidationStatus.REJECTED,
            explanation="Rejected.",
        )
        manual = OutlineEdge.objects.create(
            course=course,
            source=source,
            target=manual_target,
            score=0.42,
            is_manual=True,
            explanation="Manual.",
        )

        generate_prerequisite_edges(course)

        approved.refresh_from_db()
        rejected.refresh_from_db()
        manual.refresh_from_db()
        self.assertEqual(approved.score, 0.42)
        self.assertEqual(rejected.score, 0.42)
        self.assertEqual(manual.score, 0.42)
        self.assertEqual(approved.explanation, "Approved.")
        self.assertEqual(rejected.explanation, "Rejected.")
        self.assertEqual(manual.explanation, "Manual.")

    @patch("lessons.services.edge_generator.compute_semantic_similarity", return_value=0.80)
    def test_summary_counts_are_accurate(self, _similarity):
        course = CourseGroup.objects.create(title="Science")
        root = self.create_node(course, "Matter Module", 0)
        first_child = self.create_node(course, "States", 1, depth=1, parent=root)
        self.create_node(course, "Changes", 2, depth=1, parent=root)
        force = self.create_node(course, "Force Module", 8)
        self.create_node(course, "Motion", 1, depth=1, parent=force)

        summary = generate_prerequisite_edges(course)

        self.assertEqual(summary["total_possible_forward_pairs"], 1)
        self.assertEqual(summary["pairs_skipped_as_siblings"], 0)
        self.assertEqual(summary["pairs_skipped_by_distance"], 0)
        self.assertEqual(summary["pairs_skipped_cross_section"], 2)
        self.assertEqual(summary["pairs_skipped_cross_module"], 2)
        self.assertEqual(summary["pairs_considered_after_filtering"], 1)
        self.assertEqual(summary["candidates_above_threshold"], 1)
        self.assertEqual(summary["edges_created"], 1)
        self.assertEqual(summary["threshold"], EDGE_SCORE_THRESHOLD)
        self.assertEqual(summary["max_forward_distance"], MAX_FORWARD_DISTANCE)
        self.assertEqual(summary["max_outgoing_edges_per_node"], MAX_OUTGOING_EDGES_PER_NODE)
        self.assertEqual(summary["max_incoming_edges_per_node"], MAX_INCOMING_EDGES_PER_NODE)
        self.assertTrue(OutlineEdge.objects.filter(source=first_child, target__title="Changes").exists())

    @patch("lessons.services.edge_generator.compute_semantic_similarity", return_value=1.00)
    def test_nodes_from_another_course_are_never_included(self, _similarity):
        course = CourseGroup.objects.create(title="Science")
        other_course = CourseGroup.objects.create(title="Other")
        module = self.create_node(course, "Matter Module", 0)
        source = self.create_node(course, "Matter", 0, depth=1, parent=module)
        target = self.create_node(course, "States of Matter", 1, depth=1, parent=module)
        other_module = self.create_node(other_course, "Force Module", 0)
        other_source = self.create_node(other_course, "Force", 0, depth=1, parent=other_module)
        other_target = self.create_node(other_course, "Motion", 1, depth=1, parent=other_module)

        generate_prerequisite_edges(course)

        self.assertTrue(OutlineEdge.objects.filter(source=source, target=target).exists())
        self.assertFalse(OutlineEdge.objects.filter(source=other_source, target=other_target).exists())
        self.assertEqual(OutlineEdge.objects.filter(course=other_course).count(), 0)

    @patch("lessons.services.edge_generator.compute_semantic_similarity", return_value=1.50)
    def test_semantic_similarity_and_final_scores_stay_within_zero_to_one(self, _similarity):
        course = CourseGroup.objects.create(title="Science")
        module = self.create_node(course, "Matter Module", 0)
        source = self.create_node(course, "Matter", 0, depth=1, parent=module)
        target = self.create_node(course, "States of Matter", 1, depth=1, parent=module)

        generate_prerequisite_edges(course)

        edge = OutlineEdge.objects.get(source=source, target=target)
        self.assertGreaterEqual(edge.semantic_similarity, 0.0)
        self.assertLessEqual(edge.semantic_similarity, 1.0)
        self.assertGreaterEqual(edge.score, 0.0)
        self.assertLessEqual(edge.score, 1.0)

    def test_semantic_similarity_helper_with_fake_model_stays_in_range(self):
        class FakeModel:
            def encode(self, _titles):
                return [[1.0, 0.0], [1.0, 0.0]]

        course = CourseGroup.objects.create(title="Science")
        source = self.create_node(course, "Matter", 0)
        target = self.create_node(course, "States of Matter", 1)

        with patch("lessons.services.edge_generator._get_embedding_model", return_value=FakeModel()):
            similarity = compute_semantic_similarity(source, target)

        self.assertGreaterEqual(similarity, 0.0)
        self.assertLessEqual(similarity, 1.0)
        self.assertEqual(similarity, 1.0)
