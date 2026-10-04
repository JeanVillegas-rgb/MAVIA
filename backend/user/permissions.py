from rest_framework.permissions import BasePermission

from .models import User


class HasRole(BasePermission):
    """Base class for role-gated permissions. Subclass and set `role`."""

    role = None

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == self.role
        )


class IsAdmin(HasRole):
    role = User.Role.ADMIN


class IsTeacher(HasRole):
    role = User.Role.TEACHER


class IsStudent(HasRole):
    role = User.Role.STUDENT


class IsTeacherOrAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in (User.Role.TEACHER, User.Role.ADMIN)
        )
