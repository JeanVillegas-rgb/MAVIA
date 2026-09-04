from django.contrib import admin

from .models import CourseGroup, CourseOutline, LearningMaterial, LearningObject, OutlineNode


class OutlineNodeInline(admin.TabularInline):
    model = OutlineNode
    fk_name = "course"
    extra = 0


@admin.register(CourseGroup)
class CourseGroupAdmin(admin.ModelAdmin):
    list_display = ("title", "created_at")
    search_fields = ("title", "description")
    inlines = [OutlineNodeInline]


@admin.register(CourseOutline)
class CourseOutlineAdmin(admin.ModelAdmin):
    list_display = ("course", "uploaded_at")


@admin.register(OutlineNode)
class OutlineNodeAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "depth", "order", "parent", "published", "published_at")
    list_filter = ("course", "depth", "published")
    search_fields = ("title",)


class LearningObjectInline(admin.TabularInline):
    model = LearningObject
    extra = 0


@admin.register(LearningMaterial)
class LearningMaterialAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "status", "lesson_audio_generated", "created_at")
    list_filter = ("course", "status")
    search_fields = ("title",)
    inlines = [LearningObjectInline]

    @admin.display(boolean=True, description="Audio generated")
    def lesson_audio_generated(self, obj):
        return bool((obj.generated_json or {}).get("lesson_audio_generated"))
