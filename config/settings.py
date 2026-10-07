"""Django settings for the fuel-route-optimizer API.

Deliberately lean: this is a read-only, stateless API service - no admin,
no auth, no ORM models. All tunables come from environment variables with
sane defaults (see .env.example).
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings as django_settings  # noqa: F401  (import guard)
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

import os


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-secret-key")
DEBUG = _env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "api.apps.ApiConfig",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# The service is stateless; SQLite is only present because Django expects
# a DATABASES entry. No models are defined anywhere in this project.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR"},
        "api": {"handlers": ["console"], "level": "INFO"},
    },
}

# ---------------------------------------------------------------------------
# Vehicle model (assessment requirements)
# ---------------------------------------------------------------------------
FUEL_RANGE_MILES = _env_float("FUEL_RANGE_MILES", 500.0)
FUEL_EFFICIENCY_MPG = _env_float("FUEL_EFFICIENCY_MPG", 10.0)
FUEL_START_FULL = _env_bool("FUEL_START_FULL", True)

# ---------------------------------------------------------------------------
# Upstream free services
# ---------------------------------------------------------------------------
OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org")
OSRM_PROFILE = os.getenv("OSRM_PROFILE", "driving")
NOMINATIM_BASE_URL = os.getenv("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org")
NOMINATIM_USER_AGENT = os.getenv(
    "NOMINATIM_USER_AGENT",
    "fuel-route-optimizer-assessment/1.0 (assessment exercise)",
)

# ---------------------------------------------------------------------------
# Station corridor search
# ---------------------------------------------------------------------------
CORRIDOR_RADIUS_MILES = _env_float("CORRIDOR_RADIUS_MILES", 15.0)
CORRIDOR_MAX_STATIONS = _env_int("CORRIDOR_MAX_STATIONS", 40)

# ---------------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------------
DATA_DIR = BASE_DIR / "data"
STATIONS_CSV = DATA_DIR / "stations_enriched.csv"
GAZETTEER_CSV = DATA_DIR / "us_cities.csv"
