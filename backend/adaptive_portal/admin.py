from django.contrib import admin

from .models import Enrollment, LearningState, StudentResponse


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "course", "created_at")
    list_filter = ("course",)
    search_fields = ("student__username", "student__email")


@admin.register(LearningState)
class LearningStateAdmin(admin.ModelAdmin):
    list_display = ("student", "course", "mastery", "attempts", "completed", "updated_at")
    list_filter = ("course", "completed")
    search_fields = ("student__username",)


@admin.register(StudentResponse)
class StudentResponseAdmin(admin.ModelAdmin):
    list_display = ("learning_state", "question", "is_correct", "created_at")
    list_filter = ("is_correct",)
