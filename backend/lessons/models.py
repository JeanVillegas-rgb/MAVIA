from django.core.exceptions import ValidationError
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
    is_approved = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Outline for {self.course.title}"


class OutlineNode(models.Model):
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
    related_info = models.JSONField(default=dict, blank=True)
    order = models.PositiveIntegerField(default=0)
    depth = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["depth", "order", "id"]
        unique_together = ("course", "parent", "order")

    def __str__(self):
        return self.title


class LearningMaterial(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    course = models.ForeignKey(
        CourseGroup,
        related_name="materials",
        on_delete=models.CASCADE,
    )
    outline_node = models.ForeignKey(
        OutlineNode,
        null=True,
        blank=True,
        related_name="materials",
        on_delete=models.CASCADE,
    )
    module_node = models.ForeignKey(
        OutlineNode,
        null=True,
        blank=True,
        related_name="module_materials",
        on_delete=models.CASCADE,
    )
    title = models.CharField(max_length=255)
    pdf_file = models.FileField(upload_to="learning_materials/")
    extracted_text = models.TextField(blank=True)
    generated_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROCESSING,
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def clean(self):
        errors = {}
        if self.module_node_id:
            if self.module_node.course_id != self.course_id:
                errors["module_node"] = "Module node must belong to the same course as the material."
            if self.module_node.parent_id is not None:
                errors["module_node"] = "Module node must be a top-level outline node."
        if errors:
            raise ValidationError(errors)


class LearningObject(models.Model):
    class Kind(models.TextChoices):
        TEXT = "text", "Text"
        IMAGE = "image", "Image"

    material = models.ForeignKey(
        LearningMaterial,
        related_name="learning_objects",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=255)
    content = models.TextField()
    image_prompt = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title


class Question(models.Model):
    material = models.ForeignKey(
        LearningMaterial,
        related_name="questions",
        on_delete=models.CASCADE,
    )
    question_type = models.CharField(max_length=50, default="multiple_choice")
    prompt = models.TextField()
    choices = models.JSONField(default=list, blank=True)
    answer = models.TextField()
    explanation = models.TextField(blank=True)
    difficulty = models.CharField(max_length=50, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.prompt[:80]

