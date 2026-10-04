from django.contrib import admin

from .models import AdaptiveConfig


@admin.register(AdaptiveConfig)
class AdaptiveConfigAdmin(admin.ModelAdmin):
    list_display = [
        "p_guess",
        "p_slip",
        "p_learn",
        "mastery_ceiling",
        "starting_mastery",
        "default_difficulty",
        "updated_at",
        "updated_by",
    ]

    def has_add_permission(self, request):
        # Singleton — only ever row id=1, created lazily by AdaptiveConfig.load().
        return not AdaptiveConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
