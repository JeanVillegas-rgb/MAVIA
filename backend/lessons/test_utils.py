"""Small helpers shared by the focused lessons test modules."""

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient


def authenticated_api_client():
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username="lessons_test_teacher",
        defaults={
            "email": "lessons_test_teacher@example.com",
            "role": User.Role.TEACHER,
            "is_verified": True,
        },
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client
