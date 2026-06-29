from django.db import models


class CourseGroup(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class CourseOutline(models.Model):
    course = models.OneToOneField(
        CourseGroup,
        related_name="outline",
        on_delete=models.CASCADE,
    )
    outline_file = models.FileField(upload_to="outlines/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Outline for {self.course.title}"


class OutlineNode(models.Model):
    class NodeStatus(models.TextChoices):
        EMPTY = "empty", "Empty"
        IN_PROGRESS = "in_progress", "In Progress"
        SCRIPT_REVIEW = "script_review", "Script Review"
        AUDIO_REVIEW = "audio_review", "Audio Review"
        PUBLISHED = "published", "Published"

    course = models.ForeignKey(
        CourseGroup,
        related_name="nodes",
        on_delete=models.CASCADE,
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        related_name="children",
        on_delete=models.CASCADE,
    )
    title = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=0)
    depth = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=NodeStatus.choices,
        default=NodeStatus.EMPTY,
    )

    class Meta:
        ordering = ["depth", "order", "id"]
        unique_together = ("course", "parent", "order")

    def __str__(self):
        return self.title


class Lesson(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        SCRIPT_REVIEW = "script_review", "Script Review"
        SCRIPT_APPROVED = "script_approved", "Script Approved"
        AUDIO_GENERATING = "audio_generating", "Audio Generating"
        AUDIO_REVIEW = "audio_review", "Audio Review"
        PUBLISHED = "published", "Published"
        FAILED = "failed", "Failed"

    course = models.ForeignKey(
        CourseGroup,
        related_name="lessons",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    outline_node = models.OneToOneField(
        OutlineNode,
        related_name="lesson",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    pdf_file = models.FileField(upload_to="pdfs/")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROCESSING,
    )
    progress = models.PositiveSmallIntegerField(default=0)
    error_message = models.TextField(blank=True)
    story_intro = models.TextField(blank=True)
    script_approved_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class LessonPage(models.Model):
    lesson = models.ForeignKey(
        Lesson, on_delete=models.CASCADE, related_name="pages"
    )
    page_number = models.PositiveIntegerField()
    raw_text = models.TextField(blank=True)
    image_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["page_number"]
        unique_together = ("lesson", "page_number")

    def __str__(self):
        return f"{self.lesson.title} — page {self.page_number}"


class PageImage(models.Model):
    page = models.ForeignKey(
        LessonPage, on_delete=models.CASCADE, related_name="images"
    )
    image = models.ImageField(upload_to="page_images/")
    caption = models.TextField(blank=True)
    interpretation = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"Page {self.page.page_number} image {self.order}"


class AudioModule(models.Model):
    lesson = models.ForeignKey(
        Lesson, on_delete=models.CASCADE, related_name="audio_modules"
    )
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=255)
    narrative_text = models.TextField()
    audio_file = models.FileField(upload_to="audio_modules/", blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.lesson.title} — {self.title}"
