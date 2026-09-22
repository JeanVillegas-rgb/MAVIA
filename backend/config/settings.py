import os
import sys
import tempfile
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
    "adaptive_config",
    "learning_path",
]

AUTH_USER_MODEL = "user.User"

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
        # course's variant generation fans Ollama calls across a thread pool
        # (ADAPTIVE_VARIANT_CONCURRENCY) with writes still serialized to this
        # thread; a longer SQLite lock timeout avoids spurious "database is
        # locked" errors under that pattern.
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
if len(sys.argv) > 1 and sys.argv[1] == "test":
    # Tests upload files; they must never land in the real media folder.
    MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="mavia-test-media-"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^http://localhost:\d+$",
    r"^http://127\.0\.0\.1:\d+$",
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    # App-wide baseline: every endpoint requires a logged-in user unless it
    # opts out (register/login/verify set AllowAny; adaptive-config and the
    # lessons viewset narrow further to a role).
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    # MultiPart/Form parsers let the lessons app accept PDF uploads.
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ],
}

# URL of the React app; used to build the link inside verification emails.
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

# Whether the teacher-facing student search/enrollment picker (adaptive app)
# hides students who haven't verified their email yet. Off by default: a
# school's mail server can be slow or land verification links in spam (seen
# first-hand this session), and a teacher shouldn't lose the ability to
# enroll a real student just because delivery is flaky. Set to true if you
# want unverified accounts kept out of the roster entirely.
EMAIL_VERIFICATION_REQUIRED = os.getenv("EMAIL_VERIFICATION_REQUIRED", "False").lower() in (
    "1", "true", "yes",
)

# Email. Defaults to the console backend so verification links print to the
# runserver terminal with no setup. Set EMAIL_HOST_USER + EMAIL_HOST_PASSWORD
# in .env to switch to real SMTP (Gmail app password, etc.).
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")

if EMAIL_HOST_USER and EMAIL_HOST_PASSWORD:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
    EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
    EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() in ("1", "true", "yes")
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "no-reply@mavia.local")

# ---------------------------------------------------------------------------
# Figure explanation for blind learners (lessons app).
# When a teacher's PDF has figures, the pipeline can ask a local Ollama
# vision model what each figure *teaches* (the concept, not the layout) and
# fold that into the lesson narration. Entirely optional: if Ollama isn't
# running the pipeline falls back to each figure's caption / visible text.
#   IMAGE_DESCRIPTION_MODEL — any Ollama vision model (gemma3:4b, llava,
#     moondream, qwen2-vl, llama3.2-vision …). Must be pulled: `ollama pull …`
#   IMAGE_DESCRIPTION_ENABLED=False turns the feature off outright.
# ---------------------------------------------------------------------------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
IMAGE_DESCRIPTION_ENABLED = os.getenv("IMAGE_DESCRIPTION_ENABLED", "True").lower() in (
    "1",
    "true",
    "yes",
)
IMAGE_DESCRIPTION_MODEL = os.getenv("IMAGE_DESCRIPTION_MODEL", "gemma3:4b")
IMAGE_DESCRIPTION_TIMEOUT = int(os.getenv("IMAGE_DESCRIPTION_TIMEOUT", "300"))
IMAGE_DESCRIPTION_REACHABILITY_TTL = int(
    os.getenv("IMAGE_DESCRIPTION_REACHABILITY_TTL", "15")
)
IMAGE_DESCRIPTION_CACHE_ENABLED = os.getenv(
    "IMAGE_DESCRIPTION_CACHE_ENABLED", "True"
).lower() in ("1", "true", "yes")
IMAGE_DESCRIPTION_CACHE_PATH = os.getenv(
    "IMAGE_DESCRIPTION_CACHE_PATH",
    str(BASE_DIR / "image_description_cache" / "descriptions.sqlite3"),
)

# ---------------------------------------------------------------------------
# course / question_generation / learning_path (ported from Milestone1-Jean,
# 2026-09-15). Same Ollama server as the image-description feature above;
# OLLAMA_MODEL is the text model these use (separate from the vision model).
# ---------------------------------------------------------------------------
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", OLLAMA_MODEL)
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "240"))
OLLAMA_VISION_TIMEOUT = int(os.getenv("OLLAMA_VISION_TIMEOUT", str(OLLAMA_TIMEOUT)))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "10m")

# Questions generated per learning object, per thinking order. Lower these
# while iterating: each thinking order is one LLM call, and the count drives
# how much that call has to write.
QUESTION_COUNT_LOT = int(os.getenv("QUESTION_COUNT_LOT", "3"))
QUESTION_COUNT_HOT = int(os.getenv("QUESTION_COUNT_HOT", "3"))

ADAPTIVE_VARIANT_GENERATION_ENABLED = os.getenv(
    "ADAPTIVE_VARIANT_GENERATION_ENABLED", "True"
).lower() in ("1", "true", "yes")
ADAPTIVE_VARIANT_LLM_MODEL = os.getenv("ADAPTIVE_VARIANT_LLM_MODEL", OLLAMA_MODEL)
ADAPTIVE_VARIANT_TIMEOUT = int(os.getenv("ADAPTIVE_VARIANT_TIMEOUT", str(OLLAMA_TIMEOUT)))
# Decode budget for one simplified+elaborated pair. A learning object is
# 15-60 words and the elaborated cap is 2x source words, so ~200 words of JSON
# is the real ceiling; 1024 just meant every call ran the decoder long past the
# grounding limit. Raise it only if long source objects start truncating.
ADAPTIVE_VARIANT_NUM_PREDICT = int(os.getenv("ADAPTIVE_VARIANT_NUM_PREDICT", "512"))
# How many Ollama generation calls to keep in flight. The bottleneck in a
# publish run is N sequential calls to a local model; Ollama serves concurrent
# requests, so this is close to an N-times speed-up until it hits the server's
# own OLLAMA_NUM_PARALLEL (set that to at least this value).
ADAPTIVE_VARIANT_CONCURRENCY = int(os.getenv("ADAPTIVE_VARIANT_CONCURRENCY", "3"))

CONTENT_VERSION_LLM_ENABLED = os.getenv(
    "CONTENT_VERSION_LLM_ENABLED", "True"
).lower() in ("1", "true", "yes")
CONTENT_VERSION_LLM_MODEL = os.getenv("CONTENT_VERSION_LLM_MODEL", ADAPTIVE_VARIANT_LLM_MODEL)
CONTENT_VERSION_LLM_TIMEOUT = int(os.getenv("CONTENT_VERSION_LLM_TIMEOUT", str(OLLAMA_TIMEOUT)))
CONTENT_VERSION_LLM_AUTO_THRESHOLD = float(
    os.getenv("CONTENT_VERSION_LLM_AUTO_THRESHOLD", "0.80")
)

# Model for question generation (separate from the content generation model)
QUESTION_LLM_MODEL = os.getenv("QUESTION_LLM_MODEL", "llama3.2:3b")
QUESTION_LLM_KEEP_ALIVE = os.getenv("QUESTION_LLM_KEEP_ALIVE", "30m")
QUESTION_OVERGENERATION_FACTOR = float(
    os.getenv("QUESTION_OVERGENERATION_FACTOR", "1.0")
)

# Quiet the dev server's per-request access log; application diagnostics use
# the standard Python logging system instead. INFO reports each step of every
# pipeline; DEBUG adds the per-item detail (each drafted question, each
# duplicate or surplus dropped) that would otherwise bury it.
MAVIA_LOG_LEVEL = os.getenv("MAVIA_LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        # The pipelines already name themselves in the message, so a prefix
        # here would only repeat it.
        "trace": {"format": "%(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
        "trace": {"class": "logging.StreamHandler", "formatter": "trace"},
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
        # Progress reporting. These packages report each step through the
        # logger rather than print(), so the terminal and the teacher's
        # progress dialog are fed by the same call and cannot disagree.
        # Configured explicitly because there is no root handler: without an
        # entry here, INFO records propagate to a handler-less root and are
        # dropped by Python's last-resort handler, which passes WARNING and
        # above only.
        "lessons": {"handlers": ["trace"], "level": MAVIA_LOG_LEVEL, "propagate": False},
        "question_generation": {"handlers": ["trace"], "level": MAVIA_LOG_LEVEL, "propagate": False},
        "learning_path": {"handlers": ["trace"], "level": MAVIA_LOG_LEVEL, "propagate": False},
        "course": {"handlers": ["trace"], "level": MAVIA_LOG_LEVEL, "propagate": False},
    },
}
