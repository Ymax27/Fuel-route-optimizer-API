"""Minimal OSRM client: exactly one HTTP call per route request.

The demo server (https://router.project-osrm.org) is free and keyless. We
ask for the full geometry *plus* per-node distance annotations in the same
call, which gives us exact cumulative mileage along the route - so we never
need per-station routing calls afterwards.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import numpy as np

from routing.geo import MILES_PER_METER

OSRM_TIMEOUT_S = 20.0
OSRM_RETRIES = 2
OSRM_RETRY_BACKOFF_S = 0.6


class OSRMError(RuntimeError):
    """Raised when the routing service fails or returns unusable data."""


@dataclass(frozen=True)
class OSRMRoute:
    distance_miles: float
    duration_hours: float
    coordinates: np.ndarray        # (N, 2) float array of (lon, lat)
    cumulative_miles: np.ndarray   # (N,) miles from start, aligned with coordinates


class OSRMClient:
    def __init__(self, base_url: str, profile: str = "driving",
                 timeout_s: float = OSRM_TIMEOUT_S, retries: int = OSRM_RETRIES) -> None:
        self._base_url = base_url.rstrip("/")
        self._profile = profile
        self._timeout_s = timeout_s
        self._retries = retries
        self._client = httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        self._client.close()

    def route(self, start_lat: float, start_lon: float,
              finish_lat: float, finish_lon: float) -> OSRMRoute:
        """Fetch the driving route. Single call; light retry on 5xx/network."""
        url = f"{self._base_url}/route/v1/{self._profile}/{start_lon},{start_lat};{finish_lon},{finish_lat}"
        params = {
            "overview": "full",
            "geometries": "geojson",
            "annotations": "distance",
            "steps": "false",
            "alternatives": "false",
        }
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = self._client.get(url, params=params)
                if resp.status_code >= 500:
                    raise OSRMError(f"OSRM server error {resp.status_code}")
                resp.raise_for_status()
                return self._parse(resp.json())
            except (httpx.TransportError, OSRMError) as exc:
                last_error = exc
                if attempt < self._retries:
                    time.sleep(OSRM_RETRY_BACKOFF_S * (attempt + 1))
        raise OSRMError(f"routing service unreachable: {last_error}") from last_error

    def _parse(self, payload: dict) -> OSRMRoute:
        code = payload.get("code")
        routes = payload.get("routes") or []
        if code != "Ok" or not routes:
            raise OSRMError(f"OSRM could not route these points (code={code})")
        route = routes[0]
        coords = np.asarray(route["geometry"]["coordinates"], dtype=float)
        if coords.ndim != 2 or coords.shape[0] < 2:
            raise OSRMError("OSRM returned an empty geometry")

        # Exact per-segment distances (meters) from annotations, aligned with
        # the geometry: one entry per consecutive coordinate pair.
        seg_miles_parts: list[np.ndarray] = []
        for leg in route.get("legs", []):
            annotation = leg.get("annotation") or {}
            distances = annotation.get("distance")
            if distances is not None:
                seg_miles_parts.append(np.asarray(distances, dtype=float) * MILES_PER_METER)
        seg_miles = (np.concatenate(seg_miles_parts)
                     if seg_miles_parts else self._haversine_segments(coords))
        if seg_miles.shape[0] != coords.shape[0] - 1:
            seg_miles = self._haversine_segments(coords)  # defensive fallback

        cumulative = np.concatenate([[0.0], np.cumsum(seg_miles)])
        return OSRMRoute(
            distance_miles=float(route["distance"]) * MILES_PER_METER,
            duration_hours=float(route["duration"]) / 3600.0,
            coordinates=coords,
            cumulative_miles=cumulative,
        )

    @staticmethod
    def _haversine_segments(coords: np.ndarray) -> np.ndarray:
        from routing.geo import haversine_miles_vec
        return haversine_miles_vec(coords[:-1, 1], coords[:-1, 0], coords[1:, 1], coords[1:, 0])
