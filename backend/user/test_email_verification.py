from datetime import timedelta
from django.test import override_settings

from django.core import mail
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .models import EmailVerificationToken, User


@override_settings(EMAIL_VERIFICATION_REQUIRED=True)
class RegistrationTests(APITestCase):
    def test_register_creates_unverified_user_without_auth_token(self):
        response = self.client.post("/api/auth/register/", {
            "username": "newteacher",
            "email": "newteacher@example.com",
            "password": "StrongPass123!",
            "first_name": "New",
            "last_name": "Teacher",
            "role": User.Role.TEACHER,
        })

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("token", response.data)
        self.assertEqual(response.data["user"]["role"], User.Role.TEACHER)
        self.assertFalse(response.data["user"]["is_verified"])

        user = User.objects.get(username="newteacher")
        self.assertFalse(user.is_verified)
        self.assertTrue(user.check_password("StrongPass123!"))
        self.assertFalse(Token.objects.filter(user=user).exists())

    def test_register_sends_one_verification_email_with_a_token(self):
        self.client.post("/api/auth/register/", {
            "username": "mailcheck",
            "email": "mailcheck@example.com",
            "password": "StrongPass123!",
            "role": User.Role.STUDENT,
        })

        user = User.objects.get(username="mailcheck")
        verification = EmailVerificationToken.objects.get(user=user)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("mailcheck@example.com", mail.outbox[0].to)
        self.assertIn(verification.token, mail.outbox[0].body)

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


@override_settings(EMAIL_VERIFICATION_REQUIRED=True)
class VerifyEmailTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="pending", email="pending@example.com",
            password="CorrectHorse1!", role=User.Role.STUDENT,
        )
        self.verification = EmailVerificationToken.objects.create(
            user=self.user,
            token="valid-token-123",
            expires_at=timezone.now() + timedelta(hours=24),
        )

    def test_valid_token_verifies_user_and_consumes_token(self):
        response = self.client.get("/api/auth/verify-email/valid-token-123/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_verified)
        self.assertFalse(
            EmailVerificationToken.objects.filter(pk=self.verification.pk).exists()
        )

    def test_unknown_token_is_rejected(self):
        response = self.client.get("/api/auth/verify-email/nope/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_verified)

    def test_expired_token_is_rejected_and_deleted(self):
        self.verification.expires_at = timezone.now() - timedelta(minutes=1)
        self.verification.save()

        response = self.client.get("/api/auth/verify-email/valid-token-123/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_verified)
        self.assertFalse(
            EmailVerificationToken.objects.filter(pk=self.verification.pk).exists()
        )

    def test_reusing_a_consumed_token_is_rejected(self):
        self.client.get("/api/auth/verify-email/valid-token-123/")
        response = self.client.get("/api/auth/verify-email/valid-token-123/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(EMAIL_VERIFICATION_REQUIRED=True)
class ResendVerificationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="pending", email="pending@example.com",
            password="CorrectHorse1!", role=User.Role.STUDENT,
        )
        EmailVerificationToken.objects.create(
            user=self.user, token="old-token",
            expires_at=timezone.now() + timedelta(hours=24),
        )

    def test_resend_issues_a_fresh_token_and_email(self):
        response = self.client.post(
            "/api/auth/resend-verification/", {"email": "pending@example.com"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        token = EmailVerificationToken.objects.get(user=self.user)
        self.assertNotEqual(token.token, "old-token")
        self.assertEqual(len(mail.outbox), 1)

    def test_resend_for_unknown_email_gives_generic_ok(self):
        response = self.client.post(
            "/api/auth/resend-verification/", {"email": "ghost@example.com"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_for_already_verified_account_sends_nothing(self):
        self.user.is_verified = True
        self.user.save()

        response = self.client.post(
            "/api/auth/resend-verification/", {"email": "pending@example.com"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 0)


@override_settings(EMAIL_VERIFICATION_REQUIRED=True)
class LoginTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="learner1", password="CorrectHorse1!",
            role=User.Role.STUDENT, is_verified=True,
        )

    def test_login_with_correct_credentials_returns_token(self):
        response = self.client.post("/api/auth/login/", {
            "username": "learner1",
            "password": "CorrectHorse1!",
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["token"], Token.objects.get(user=self.user).key)
        self.assertEqual(response.data["user"]["username"], "learner1")

    def test_login_is_blocked_until_email_is_verified(self):
        unverified = User.objects.create_user(
            username="notyet", password="CorrectHorse1!", role=User.Role.STUDENT,
        )
        self.assertFalse(unverified.is_verified)

        response = self.client.post("/api/auth/login/", {
            "username": "notyet",
            "password": "CorrectHorse1!",
        })

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Token.objects.filter(user=unverified).exists())

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


@override_settings(EMAIL_VERIFICATION_REQUIRED=True)
class MeAndLogoutTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="learner2", password="CorrectHorse1!",
            role=User.Role.STUDENT, is_verified=True,
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

        response = self.client.get("/api/auth/me/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_requires_authentication(self):
        response = self.client.post("/api/auth/logout/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
