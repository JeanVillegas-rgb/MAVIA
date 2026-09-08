from django.conf import settings
from django.core.mail import send_mail


def send_verification_email(email, token):
    """Email the user a link that hits the frontend's /verify-email/<token> route,
    which in turn calls the verify-email API endpoint."""
    verification_url = f"{settings.FRONTEND_URL}/verify-email/{token}"
    send_mail(
        subject="Verify your MAVIA account",
        message=(
            "Welcome to MAVIA!\n\n"
            f"Click the link below to verify your account:\n{verification_url}\n\n"
            "This link expires in 24 hours. If you did not create an account, "
            "you can ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )
