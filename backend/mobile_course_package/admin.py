from django.contrib import admin

from .models import StudentResponse, TopicPackage, TopicPackageProgress


@admin.register(TopicPackage)
class TopicPackageAdmin(admin.ModelAdmin):
    list_display = ("topic", "published_at", "built_at")


@admin.register(TopicPackageProgress)
class TopicPackageProgressAdmin(admin.ModelAdmin):
    list_display = ("student", "topic", "current_step_position", "current_variant", "return_to_position", "completed", "updated_at")
    list_filter = ("completed", "current_variant")


@admin.register(StudentResponse)
class StudentResponseAdmin(admin.ModelAdmin):
    list_display = ("student", "topic", "question_id", "selected_answer", "is_correct", "variant", "attempt_number", "created_at")
    list_filter = ("is_correct", "variant")
