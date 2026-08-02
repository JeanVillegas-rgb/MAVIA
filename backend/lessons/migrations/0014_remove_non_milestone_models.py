# Generated for milestone scope cleanup on 2026-07-25

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0013_learningobjectedge_relation_type"),
    ]

    operations = [
        migrations.DeleteModel(name="ModuleConceptDAGState"),
        migrations.DeleteModel(name="ConceptPrerequisiteEdge"),
        migrations.DeleteModel(name="ConceptSource"),
        migrations.DeleteModel(name="ExtractedConcept"),
        migrations.DeleteModel(name="LearningObjectPrerequisiteEdge"),
        migrations.DeleteModel(name="OutlineEdge"),
    ]
