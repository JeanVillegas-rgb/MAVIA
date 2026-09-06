from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    AdminProfile,
    EmailVerificationToken,
    StudentProfile,
    TeacherProfile,
    User,
)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ["username", "email", "role", "is_verified", "is_staff", "is_active"]
    list_filter = ["role", "is_verified", "is_staff", "is_active"]
    fieldsets = UserAdmin.fieldsets + (
        ("Role", {"fields": ("role", "is_verified")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Role", {"fields": ("role",)}),
    )


@admin.register(EmailVerificationToken)
class EmailVerificationTokenAdmin(admin.ModelAdmin):
    list_display = ["user", "created_at", "expires_at"]
    search_fields = ["user__username", "user__email"]


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ["user"]
    search_fields = ["user__username", "user__email"]


@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = ["user"]
    search_fields = ["user__username", "user__email"]


@admin.register(AdminProfile)
class AdminProfileAdmin(admin.ModelAdmin):
    list_display = ["user"]
    search_fields = ["user__username", "user__email"]
