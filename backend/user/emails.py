from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

# Keep in sync with VERIFICATION_TTL in user/views.py.
VERIFICATION_EXPIRY_HOURS = 24


def send_verification_email(email, token):
    """Email the user a link that hits the frontend's /verify-email/<token> route,
    which in turn calls the verify-email API endpoint.

    Sends a multipart message: an HTML layout for clients that render it, plus a
    plain-text fallback. Edit the templates in user/templates/user/emails/ to
    change the look.
    """
    verification_url = f"{settings.FRONTEND_URL}/verify-email/{token}"
    context = {
        "verification_url": verification_url,
        "expiry_hours": VERIFICATION_EXPIRY_HOURS,
    }

    text_body = render_to_string("user/emails/verify_email.txt", context)
    html_body = render_to_string("user/emails/verify_email.html", context)

    message = EmailMultiAlternatives(
        subject="Verify your MAVIA account",
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    message.attach_alternative(html_body, "text/html")
    message.send(fail_silently=False)
