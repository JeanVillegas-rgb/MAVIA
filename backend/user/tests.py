from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .models import User


class RegistrationTests(APITestCase):
    def test_register_creates_user_and_returns_token(self):
        response = self.client.post("/api/auth/register/", {
            "username": "newteacher",
            "email": "newteacher@example.com",
            "password": "StrongPass123!",
            "first_name": "New",
            "last_name": "Teacher",
            "role": User.Role.TEACHER,
        })

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("token", response.data)
        self.assertEqual(response.data["user"]["username"], "newteacher")
        self.assertEqual(response.data["user"]["role"], User.Role.TEACHER)

        user = User.objects.get(username="newteacher")
        self.assertTrue(user.check_password("StrongPass123!"))
        self.assertEqual(Token.objects.get(user=user).key, response.data["token"])

    def test_register_rejects_self_service_admin_role(self):
        response = self.client.post("/api/auth/register/", {
            "username": "sneaky",
            "email": "sneaky@example.com",
            "password": "StrongPass123!",
            "role": User.Role.ADMIN,
        })

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(username="sneaky").exists())

    def test_register_rejects_duplicate_username(self):
        User.objects.create_user(username="taken", password="x", role=User.Role.STUDENT)

        response = self.client.post("/api/auth/register/", {
            "username": "taken",
            "email": "other@example.com",
            "password": "StrongPass123!",
            "role": User.Role.STUDENT,
        })

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_rejects_weak_password(self):
        response = self.client.post("/api/auth/register/", {
            "username": "weakpass",
            "email": "weak@example.com",
            "password": "123",
            "role": User.Role.STUDENT,
        })

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(username="weakpass").exists())


class LoginTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="learner1", password="CorrectHorse1!", role=User.Role.STUDENT,
        )

    def test_login_with_correct_credentials_returns_token(self):
        response = self.client.post("/api/auth/login/", {
            "username": "learner1",
            "password": "CorrectHorse1!",
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["token"], Token.objects.get(user=self.user).key)
        self.assertEqual(response.data["user"]["username"], "learner1")

    def test_login_with_wrong_password_is_rejected(self):
        response = self.client.post("/api/auth/login/", {
            "username": "learner1",
            "password": "wrong-password",
        })

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_with_unknown_username_is_rejected(self):
        response = self.client.post("/api/auth/login/", {
            "username": "ghost",
            "password": "whatever",
        })

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_reissues_same_token_on_repeat_login(self):
        first = self.client.post("/api/auth/login/", {
            "username": "learner1", "password": "CorrectHorse1!",
        })
        second = self.client.post("/api/auth/login/", {
            "username": "learner1", "password": "CorrectHorse1!",
        })
        self.assertEqual(first.data["token"], second.data["token"])


class MeAndLogoutTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="learner2", password="CorrectHorse1!", role=User.Role.STUDENT,
        )
        self.token = Token.objects.create(user=self.user)

    def _authed(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def test_me_requires_authentication(self):
        response = self.client.get("/api/auth/me/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_returns_current_user_when_authenticated(self):
        self._authed()
        response = self.client.get("/api/auth/me/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "learner2")

    def test_logout_deletes_token_so_it_can_no_longer_authenticate(self):
        self._authed()
        response = self.client.post("/api/auth/logout/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Token.objects.filter(user=self.user).exists())

        # the same (now-deleted) token must no longer work
        response = self.client.get("/api/auth/me/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_requires_authentication(self):
        response = self.client.post("/api/auth/logout/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
