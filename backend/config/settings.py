import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-mavia-dev-key-change-in-production",
)

DEBUG = os.getenv("DJANGO_DEBUG", "True").lower() in ("1", "true", "yes")

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    "user",
    "lessons",
    "question_generation",
    "course",
    "adaptive",
    "adaptive_portal",
    "adaptive_config",
]

AUTH_USER_MODEL = "user.User"

# Optional verification keeps existing local registration usable without SMTP.
EMAIL_VERIFICATION_REQUIRED = os.getenv("EMAIL_VERIFICATION_REQUIRED", "False").lower() in ("1", "true", "yes")
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() in ("1", "true", "yes")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "noreply@mavia.local")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
IMAGE_DESCRIPTION_ENABLED = os.getenv("IMAGE_DESCRIPTION_ENABLED", "True").lower() in ("1", "true", "yes")
IMAGE_DESCRIPTION_MODEL = os.getenv("IMAGE_DESCRIPTION_MODEL", "gemma3:4b")
IMAGE_DESCRIPTION_TIMEOUT = int(os.getenv("IMAGE_DESCRIPTION_TIMEOUT", "300"))

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {"timeout": 20},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8081,http://127.0.0.1:8081,http://localhost:8082,http://127.0.0.1:8082",
    ).split(",")
    if origin.strip()
]

CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^http://localhost:\d+$",
    r"^http://127\.0\.0\.1:\d+$",
]

REST_FRAMEWORK = {
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
}

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", OLLAMA_MODEL)
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "240"))
OLLAMA_VISION_TIMEOUT = int(os.getenv("OLLAMA_VISION_TIMEOUT", str(OLLAMA_TIMEOUT)))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "10m")

ADAPTIVE_VARIANT_GENERATION_ENABLED = os.getenv(
    "ADAPTIVE_VARIANT_GENERATION_ENABLED", "True"
).lower() in ("1", "true", "yes")
ADAPTIVE_VARIANT_LLM_MODEL = os.getenv("ADAPTIVE_VARIANT_LLM_MODEL", OLLAMA_MODEL)
ADAPTIVE_VARIANT_TIMEOUT = int(os.getenv("ADAPTIVE_VARIANT_TIMEOUT", str(OLLAMA_TIMEOUT)))

# Model for question generation (separate from the content generation model)
QUESTION_LLM_MODEL = os.getenv("QUESTION_LLM_MODEL", "llama3.2:3b")


# Quiet the dev server's per-request access log (e.g. the frontend's
# generation-trace polling floods it at 200 OK / INFO). 4xx/5xx still show
# since runserver logs those at WARNING/ERROR; only successful requests are
# silenced. Application diagnostics use the standard Python logging system.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "django.server": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "lessons.views": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
