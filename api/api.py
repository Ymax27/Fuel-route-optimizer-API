"""django-ninja API: the route endpoint, health check and OpenAPI docs."""

from __future__ import annotations

import hashlib
import json
import logging
import time

import django
from django.conf import settings
from django.core.cache import cache
from ninja import NinjaAPI

from api.container import get_pipeline
from api.schemas import RouteResponse
from api.serializers import build_response
from api.staticmap import build_static_map_url
from routing.geocoder import GeocodeError
from routing.pipeline import PipelineError, RouteError

logger = logging.getLogger(__name__)

api = NinjaAPI(
    title="Fuel Route Optimizer API",
    version="1.0.0",
    description=(
        "Given a start and finish within the USA, returns the driving route, "
        "the cost-optimal fuel stops for a 500-mile-range / 10 mpg vehicle, "
        "and the total fuel spend."
    ),
)

_CACHE_TTL_S = 30 * 60  # responses are deterministic; cache for 30 minutes


@api.get("/route", response=RouteResponse)
def plan_route(request, start: str, finish: str):
    """Plan the cheapest refueling strategy for a US road trip.

    `start` / `finish` accept free-text places ("Cincinnati, OH") or
    "lat,lon" pairs. One OSRM call per uncached request; station lookup,
    corridor projection and optimization are all local and sub-millisecond.
    """
    pipeline = get_pipeline()
    start_q, finish_q = start.strip(), finish.strip()
    # Memcached-safe key: hash the normalized query instead of embedding it.
    digest = hashlib.md5(f"{start_q.casefold()}|{finish_q.casefold()}".encode()).hexdigest()
    cache_key = f"fuelroute:v1:{digest}"
    t0 = time.perf_counter()

    cached = cache.get(cache_key)
    if cached is not None:
        payload = json.loads(cached)
        payload["cached"] = True
        # Report the actual serving latency of THIS request, not the original solve.
        payload["timing_ms"] = {"total": round((time.perf_counter() - t0) * 1000, 1)}
        cache.set(f"fuelroute:idx:{payload['request_id']}", cache_key, _CACHE_TTL_S)
        return payload

    result = pipeline.run(start_q, finish_q)
    static_url = build_static_map_url(
        result.coordinates,
        result.start_coords,
        result.finish_coords,
        [(s.station.station.lat, s.station.station.lon) for s in result.plan.stops],
    )
    response = build_response(result, vehicle={
        "range_miles": settings.FUEL_RANGE_MILES,
        "mpg": settings.FUEL_EFFICIENCY_MPG,
        "start_with_full_tank": settings.FUEL_START_FULL,
    }, static_url=static_url)

    cache.set(cache_key, response.json(), _CACHE_TTL_S)
    cache.set(f"fuelroute:idx:{result.request_id}", cache_key, _CACHE_TTL_S)
    logger.info(
        "route %s -> %s: %.0f mi, $%.2f, %d stops in %s ms",
        start_q, finish_q, result.route_miles, result.plan.total_cost,
        len(result.plan.stops), result.timing_ms["total"],
    )
    return response


@api.get("/health")
def health(request):
    """Liveness probe with dataset stats."""
    pipeline = get_pipeline()
    return {
        "status": "ok",
        "django_version": django.get_version(),
        "stations_indexed": pipeline.store.size,
        "osrm_base_url": settings.OSRM_BASE_URL,
        "vehicle": {
            "range_miles": settings.FUEL_RANGE_MILES,
            "mpg": settings.FUEL_EFFICIENCY_MPG,
        },
    }


# --- error handling ---------------------------------------------------------

@api.exception_handler(GeocodeError)
def geocode_error_handler(request, exc: GeocodeError):
    return api.create_response(request, {"detail": str(exc)}, status=400)


@api.exception_handler(RouteError)
def route_error_handler(request, exc: RouteError):
    return api.create_response(request, {"detail": str(exc)}, status=502)


@api.exception_handler(PipelineError)
def pipeline_error_handler(request, exc: PipelineError):
    return api.create_response(request, {"detail": str(exc)}, status=422)
