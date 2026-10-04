import secrets
from datetime import timedelta

from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView

from .emails import send_verification_email
from .models import EmailVerificationToken, User
from .serializers import LoginSerializer, RegisterSerializer, UserSerializer

VERIFICATION_TTL = timedelta(hours=24)


def _issue_verification(user):
    """Create (or replace) the user's verification token and email it out."""
    verification, _ = EmailVerificationToken.objects.update_or_create(
        user=user,
        defaults={
            "token": secrets.token_urlsafe(32),
            "expires_at": timezone.now() + VERIFICATION_TTL,
        },
    )
    send_verification_email(user.email, verification.token)
    return verification


# The four public endpoints below authenticate nobody, on purpose.
#
# `AllowAny` says "no permission required"; it does not stop DRF *running* the
# default authenticators first. `SessionAuthentication` is one of them, and it
# enforces CSRF as soon as a session cookie identifies an active user. Cookies
# are scoped to a host, not a port, so opening Django admin on :8000 leaves a
# session cookie the web app on :5173 sends with every call -- and signing up
# then fails with "CSRF Failed: Origin checking failed", which reads like a
# broken form. Seen 2026-09-22, right after a `createsuperuser` and an admin
# login.
#
# Emptying `authentication_classes` is the fix rather than trusting more
# origins: whoever happens to be logged in has nothing to do with registering,
# logging in, or clicking a link in a verification email, so these views should
# not look at a session at all. Authenticated endpoints are unaffected -- they
# send `Authorization: Token ...`, which `TokenAuthentication` accepts first,
# so `SessionAuthentication` never runs for them either.
PUBLIC_ENDPOINT_AUTHENTICATION = []


class RegisterView(generics.CreateAPIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = PUBLIC_ENDPOINT_AUTHENTICATION
    serializer_class = RegisterSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        _issue_verification(user)
        return Response(
            {
                "message": (
                    "Registration successful. Check your email for a link to "
                    "verify your account before logging in."
                ),
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class VerifyEmailView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = PUBLIC_ENDPOINT_AUTHENTICATION

    def get(self, request, token):
        verification = EmailVerificationToken.objects.filter(token=token).first()

        if verification is None:
            return Response(
                {"detail": "This link is no longer valid. It may have already been used."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = verification.user

        if user.is_verified:
            verification.delete()
            return Response({"message": "Email already verified. You can log in."})

        if verification.is_expired():
            verification.delete()
            return Response(
                {"detail": "Verification link has expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.is_verified = True
        user.save(update_fields=["is_verified"])
        verification.delete()
        return Response({"message": "Email verified. You can now log in."})


class ResendVerificationView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = PUBLIC_ENDPOINT_AUTHENTICATION

    def post(self, request):
        email = request.data.get("email")
        user = User.objects.filter(email=email).first() if email else None

        # Don't reveal whether an address is registered.
        if user is None or user.is_verified:
            return Response(
                {"message": "If that account exists and is unverified, a new link is on its way."}
            )

        _issue_verification(user)
        return Response(
            {"message": "If that account exists and is unverified, a new link is on its way."}
        )


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = PUBLIC_ENDPOINT_AUTHENTICATION

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user": UserSerializer(user).data})


class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        request.user.auth_token.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user
