from django.contrib import admin

from .models import AudioModule, CourseGroup, CourseOutline, Lesson, LessonPage, OutlineNode, PageImage


class PageImageInline(admin.TabularInline):
    model = PageImage
    extra = 0


class LessonPageInline(admin.TabularInline):
    model = LessonPage
    extra = 0


class AudioModuleInline(admin.TabularInline):
    model = AudioModule
    extra = 0


class OutlineNodeInline(admin.TabularInline):
    model = OutlineNode
    fk_name = "course"
    extra = 0


@admin.register(CourseGroup)
class CourseGroupAdmin(admin.ModelAdmin):
    list_display = ("title", "created_at")
    inlines = [OutlineNodeInline]


@admin.register(OutlineNode)
class OutlineNodeAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "depth", "order", "status")


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "status", "progress", "created_at")
    inlines = [LessonPageInline, AudioModuleInline]
