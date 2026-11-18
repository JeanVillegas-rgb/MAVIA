from django.contrib import admin

from .models import ConceptMastery, Decision, Enrollment, StudentBaseline


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "course", "created_at")
    list_filter = ("course",)
    search_fields = ("student__username", "student__email")


@admin.register(StudentBaseline)
class StudentBaselineAdmin(admin.ModelAdmin):
    # the cold-start demo screen: watch a stranger's estimates move until calibrated
    list_display = ("student", "course", "total_responses_count", "estimated_ability", "estimated_l0", "calibrated", "calibrated_at")
    list_filter = ("course", "calibrated")


@admin.register(ConceptMastery)
class ConceptMasteryAdmin(admin.ModelAdmin):
    list_display = ("student", "concept_id", "mastery_score", "updated_at")


@admin.register(Decision)
class DecisionAdmin(admin.ModelAdmin):
    list_display = ("response", "action", "predicted_correct", "mastery_before", "mastery_after", "p_guess_used", "baseline_calibrated")
    list_filter = ("action", "baseline_calibrated")
