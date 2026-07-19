# Generated migration for course workflow

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lessons", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CourseGroup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="CourseOutline",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("outline_file", models.FileField(upload_to="outlines/")),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("course", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="outline", to="lessons.coursegroup")),
            ],
        ),
        migrations.CreateModel(
            name="OutlineNode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("order", models.PositiveIntegerField(default=0)),
                ("depth", models.PositiveSmallIntegerField(default=0)),
                ("status", models.CharField(choices=[("empty", "Empty"), ("in_progress", "In Progress"), ("script_review", "Script Review"), ("audio_review", "Audio Review"), ("published", "Published")], default="empty", max_length=20)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="nodes", to="lessons.coursegroup")),
                ("parent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="children", to="lessons.outlinenode")),
            ],
            options={"ordering": ["depth", "order", "id"], "unique_together": {("course", "parent", "order")}},
        ),
        migrations.AddField(
            model_name="lesson",
            name="course",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="lessons", to="lessons.coursegroup"),
        ),
        migrations.AddField(
            model_name="lesson",
            name="outline_node",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="lesson", to="lessons.outlinenode"),
        ),
        migrations.AddField(
            model_name="lesson",
            name="published_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="lesson",
            name="script_approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="lesson",
            name="status",
            field=models.CharField(choices=[("processing", "Processing"), ("script_review", "Script Review"), ("script_approved", "Script Approved"), ("audio_generating", "Audio Generating"), ("audio_review", "Audio Review"), ("published", "Published"), ("failed", "Failed")], default="processing", max_length=20),
        ),
    ]
