import uuid
from copy import copy

from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import CourseGroup, CourseOutline, LearningMaterial, LearningObject
from .serializers import CourseDetailSerializer, LearningMaterialSerializer, LearningObjectSerializer


class MetadataIdTests(TestCase):
    def setUp(self):
        self.course = CourseGroup.objects.create(title="Science")
        self.outline = CourseOutline.objects.create(course=self.course, outline_file="outlines/test.pdf")
        self.material = LearningMaterial.objects.create(course=self.course, title="Matter")
        self.obj = LearningObject.objects.create(material=self.material, title="Solid", content="Keeps its shape.")

    def test_unique_uuid_defaults_and_stability(self):
        records = [self.outline, self.material, self.obj]
        self.assertEqual(len({row.metadata_id for row in records}), 3)
        for row in records:
            with self.subTest(model=type(row).__name__):
                original = row.metadata_id
                self.assertIsInstance(original, uuid.UUID)
                self.assertEqual(original.version, 4)
                original_pk = row.pk
                row.save()
                row.refresh_from_db()
                self.assertEqual(row.metadata_id, original)
                self.assertEqual(row.pk, original_pk)
                self.assertFalse(row._meta.get_field("metadata_id").editable)

    def test_database_rejects_duplicates(self):
        for row in [self.outline, self.material, self.obj]:
            with self.subTest(model=type(row).__name__):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    duplicate = copy(row)
                    duplicate.pk = None
                    duplicate.save(force_insert=True)

    def test_api_exposes_ids_without_replacing_numeric_ids(self):
        outline = CourseDetailSerializer(self.course).data["outline"]
        self.assertEqual(outline["metadata_id"], str(self.outline.metadata_id))
        self.assertEqual(outline["files"][0]["metadata_id"], str(self.outline.metadata_id))
        for record, serializer in [(self.material, LearningMaterialSerializer), (self.obj, LearningObjectSerializer)]:
            data = serializer(record).data
            self.assertEqual(data["metadata_id"], str(record.metadata_id))
            self.assertEqual(data["id"], record.pk)
            self.assertTrue(serializer().fields["metadata_id"].read_only)

    def test_bulk_created_objects_get_distinct_ids(self):
        rows = LearningObject.objects.bulk_create([
            LearningObject(material=self.material, title="Liquid", content="Flows"),
            LearningObject(material=self.material, title="Gas", content="Expands"),
        ])
        self.assertNotEqual(rows[0].metadata_id, rows[1].metadata_id)
