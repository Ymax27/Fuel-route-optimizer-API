"""Composition root: build the pipeline from Django settings once per process."""

from __future__ import annotations

from django.conf import settings

from routing.geocoder import Geocoder
from routing.osrm import OSRMClient
from routing.pipeline import FuelRoutePipeline
from routing.stations import StationStore, load_stations

_pipeline: FuelRoutePipeline | None = None


def build_pipeline() -> FuelRoutePipeline:
    store = StationStore(load_stations(settings.STATIONS_CSV))
    osrm = OSRMClient(base_url=settings.OSRM_BASE_URL, profile=settings.OSRM_PROFILE)
    geocoder = Geocoder(
        base_url=settings.NOMINATIM_BASE_URL,
        user_agent=settings.NOMINATIM_USER_AGENT,
    )
    return FuelRoutePipeline(
        store=store,
        osrm=osrm,
        geocoder=geocoder,
        corridor_radius_miles=settings.CORRIDOR_RADIUS_MILES,
        corridor_max_stations=settings.CORRIDOR_MAX_STATIONS,
        mpg=settings.FUEL_EFFICIENCY_MPG,
        range_miles=settings.FUEL_RANGE_MILES,
        start_full=settings.FUEL_START_FULL,
    )


def get_pipeline() -> FuelRoutePipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = build_pipeline()
    return _pipeline
