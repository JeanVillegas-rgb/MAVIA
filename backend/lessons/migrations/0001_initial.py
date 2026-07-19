from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Lesson",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("pdf_file", models.FileField(upload_to="pdfs/")),
                ("status", models.CharField(choices=[("pending", "Pending"), ("processing", "Processing"), ("completed", "Completed"), ("failed", "Failed")], default="pending", max_length=20)),
                ("progress", models.PositiveSmallIntegerField(default=0)),
                ("error_message", models.TextField(blank=True)),
                ("story_intro", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="LessonPage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("page_number", models.PositiveIntegerField()),
                ("raw_text", models.TextField(blank=True)),
                ("image_count", models.PositiveIntegerField(default=0)),
                ("lesson", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pages", to="lessons.lesson")),
            ],
            options={"ordering": ["page_number"], "unique_together": {("lesson", "page_number")}},
        ),
        migrations.CreateModel(
            name="PageImage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("image", models.ImageField(upload_to="page_images/")),
                ("caption", models.TextField(blank=True)),
                ("interpretation", models.TextField(blank=True)),
                ("order", models.PositiveIntegerField(default=0)),
                ("page", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="images", to="lessons.lessonpage")),
            ],
            options={"ordering": ["order"]},
        ),
        migrations.CreateModel(
            name="AudioModule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order", models.PositiveIntegerField(default=0)),
                ("title", models.CharField(max_length=255)),
                ("narrative_text", models.TextField()),
                ("audio_file", models.FileField(blank=True, upload_to="audio_modules/")),
                ("duration_seconds", models.FloatField(blank=True, null=True)),
                ("lesson", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="audio_modules", to="lessons.lesson")),
            ],
            options={"ordering": ["order"]},
        ),
    ]
