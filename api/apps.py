"""API app configuration: builds the process-wide pipeline at startup."""

from __future__ import annotations

import logging

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"

    def ready(self) -> None:
        # Import inside ready() so apps are fully loaded first.
        from api.container import build_pipeline

        self.pipeline = build_pipeline()
        store = self.pipeline.store
        logger.info(
            "fuel-route pipeline ready: %d stations indexed, OSRM=%s",
            store.size,
            settings.OSRM_BASE_URL,
        )
