"""Settings for the AirMax air quality PoC.

Everything environment-specific comes from the environment, loaded from a gitignored
`.env` at the repo root. Nothing here carries a credential.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_flag(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = env_flag("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_vite",
    "airmax",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves built assets when DEBUG is off, so the demo needs no separate web server.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "webappconf.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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

WSGI_APPLICATION = "webappconf.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ["DB_HOST"],
        "PORT": os.environ.get("DB_PORT", "5432"),
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        # RDS runs with rds.force_ssl = 1; a plaintext connection is refused outright
        # with "no pg_hba.conf entry ... no encryption", which reads like a bad password.
        "OPTIONS": {"sslmode": "require"},
        "CONN_MAX_AGE": 60,
    }
}

AUTH_USER_MODEL = "airmax.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "airmax:home"
LOGOUT_REDIRECT_URL = "login"

MAILERS = {
    "default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"},
}

LANGUAGE_CODE = "en-GB"
USE_I18N = True
# `USE_TZ` — not `TIME_ZONE` — is what puts UTC in the database: every datetime is stored in
# a `timestamptz` column as UTC whatever offset it arrived on, so DST can never shift a
# reading or a window bound.
#
# `TIME_ZONE` is the *rendering* frame, and Brussels is right for it: a reading at a Belgian
# station is a fact about Belgian air at a Belgian hour, true whoever is looking.
#
# The catch is that outside a request there is no active zone, so this is also the frame that
# `make_aware()` assumes. Ingestion must therefore attach UTC explicitly and never lean on the
# ambient zone — the payload's `date.local` carries a real offset, so it has no excuse to.
TIME_ZONE = "Europe/Brussels"
USE_THOUSAND_SEPARATOR = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# The demo-morning guardrail: with dev_mode off, assets come from the Vite manifest and
# no Node process needs to be alive. Binding it to DEBUG means the two can never drift.
DJANGO_VITE = {
    "default": {
        "dev_mode": DEBUG,
        "static_url_prefix": "dist",
        "manifest_path": BASE_DIR / "static" / "dist" / "manifest.json",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
