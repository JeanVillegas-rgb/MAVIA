from django.test import TestCase

from lessons.models import CourseGroup, LearningMaterial, LearningObject, OutlineNode

from .models import CourseModule, LessonNode, LessonVariant
from .services import LessonPackageService


class PackageVersionTests(TestCase):
    def setUp(self):
        course = CourseGroup.objects.create(title="Grade 1 Science")
        root = OutlineNode.objects.create(course=course, title="Matter", order=0, depth=0)
        self.material = LearningMaterial.objects.create(
            course=course, outline_node=root, title="Lesson 1"
        )
        module = CourseModule.objects.create(source=root)
        self.node = LessonNode.objects.create(module=module, source=self.material)
        self.original = LearningObject.objects.create(
            material=self.material, title="Solid", content="A solid holds its shape.", order=0
        )
        self.partner = LearningObject.objects.create(
            material=self.material, title="Solid again", content="A solid keeps a fixed shape.", order=1
        )

    def test_represented_objects_are_not_chunks(self):
        self.partner.represented_by = self.original
        self.partner.save()

        package = LessonPackageService.build_package(self.node.id)

        ids = [chunk["id"] for chunk in package["chunks"]]
        self.assertIn(self.original.id, ids)
        self.assertNotIn(self.partner.id, ids)

    def test_chunk_reports_variant_origin(self):
        LessonVariant.objects.create(
            learning_object=self.original,
            variant="SIMPLIFIED",
            narration="Solid keeps shape.",
            origin="source_pdf",
            source_learning_object=self.partner,
        )

        package = LessonPackageService.build_package(self.node.id)
        chunk = next(c for c in package["chunks"] if c["id"] == self.original.id)

        self.assertEqual(chunk["variants"]["simplified"]["origin"], "source_pdf")
        self.assertEqual(chunk["variants"]["normal"]["origin"], "original")

    def test_chunk_flags_incomplete_versions(self):
        package = LessonPackageService.build_package(self.node.id)
        chunk = next(c for c in package["chunks"] if c["id"] == self.original.id)
        self.assertFalse(chunk["versions_complete"])

    def test_chunk_is_complete_with_both_slots(self):
        for slot in ("SIMPLIFIED", "ELABORATED"):
            LessonVariant.objects.create(
                learning_object=self.original, variant=slot, narration=f"{slot} text", origin="generated"
            )

        package = LessonPackageService.build_package(self.node.id)
        chunk = next(c for c in package["chunks"] if c["id"] == self.original.id)

        self.assertTrue(chunk["versions_complete"])

    def test_extras_are_not_offered_as_a_version(self):
        LessonVariant.objects.create(
            learning_object=self.original, variant="EXTRA", narration="Third wording", origin="source_pdf"
        )
        package = LessonPackageService.build_package(self.node.id)
        chunk = next(c for c in package["chunks"] if c["id"] == self.original.id)
        self.assertNotIn("extra", chunk["variants"])
