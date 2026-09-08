from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from user.models import User

from .models import AdaptiveConfig


class AdaptiveConfigTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin1", password="x", role=User.Role.ADMIN, is_verified=True,
        )
        self.teacher = User.objects.create_user(
            username="teacher1", password="x", role=User.Role.TEACHER, is_verified=True,
        )
        self.admin_token = Token.objects.create(user=self.admin)
        self.teacher_token = Token.objects.create(user=self.teacher)

    def _as(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_get_returns_defaults_when_unset(self):
        self._as(self.admin_token)
        response = self.client.get("/api/adaptive-config/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertAlmostEqual(response.data["p_guess"], 0.20)
        self.assertAlmostEqual(response.data["p_slip"], 0.10)
        self.assertAlmostEqual(response.data["p_learn"], 0.15)
        self.assertAlmostEqual(response.data["mastery_ceiling"], 0.99)
        self.assertAlmostEqual(response.data["starting_mastery"], 0.30)
        self.assertEqual(response.data["default_difficulty"], "medium")

    def test_non_admin_cannot_read_or_write(self):
        self._as(self.teacher_token)

        get_response = self.client.get("/api/adaptive-config/")
        self.assertEqual(get_response.status_code, status.HTTP_403_FORBIDDEN)

        patch_response = self.client.patch("/api/adaptive-config/", {"p_guess": 0.5})
        self.assertEqual(patch_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_cannot_read(self):
        response = self.client.get("/api/adaptive-config/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_can_update_weights_and_it_persists(self):
        self._as(self.admin_token)
        response = self.client.patch("/api/adaptive-config/", {
            "p_guess": 0.25,
            "p_slip": 0.05,
            "p_learn": 0.20,
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertAlmostEqual(response.data["p_guess"], 0.25)
        self.assertEqual(response.data["updated_by_username"], "admin1")

        config = AdaptiveConfig.load()
        self.assertAlmostEqual(config.p_slip, 0.05)
        self.assertEqual(config.updated_by, self.admin)

    def test_rejects_out_of_range_values(self):
        self._as(self.admin_token)
        response = self.client.patch("/api/adaptive-config/", {"p_guess": 1.5})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_p_guess_plus_p_slip_at_or_above_one(self):
        self._as(self.admin_token)
        response = self.client.patch("/api/adaptive-config/", {
            "p_guess": 0.6,
            "p_slip": 0.5,
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_starting_mastery_above_ceiling(self):
        self._as(self.admin_token)
        response = self.client.patch("/api/adaptive-config/", {
            "starting_mastery": 0.9,
            "mastery_ceiling": 0.5,
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reset_restores_defaults(self):
        self._as(self.admin_token)
        self.client.patch("/api/adaptive-config/", {"p_guess": 0.9})

        response = self.client.post("/api/adaptive-config/reset/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertAlmostEqual(response.data["p_guess"], 0.20)
        self.assertEqual(AdaptiveConfig.objects.count(), 1)
