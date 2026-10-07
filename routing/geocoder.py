"""Geocode free-text locations (start/finish) via Nominatim.

Nominatim is free and keyless but rate-limited (1 req/s) and requires a
descriptive User-Agent. Results are cached in-process keyed by the query
string, so repeated requests for the same city never hit the upstream
again. Addresses are also accepted directly as "lat,lon" to skip the call.
"""

from __future__ import annotations

import httpx

from routing.geo import haversine_miles

# Rough US bounds (contiguous + AK/HI buffer). Anything outside is rejected
# since the exercise scopes the problem to the USA.
US_BOUNDS = {"min_lat": 17.0, "max_lat": 72.0, "min_lon": -180.0, "max_lon": -64.0}


class GeocodeError(ValueError):
    """Raised when a location cannot be resolved inside the USA."""


class Geocoder:
    def __init__(self, base_url: str, user_agent: str) -> None:
        self._url = base_url.rstrip("/") + "/search"
        self._client = httpx.Client(timeout=15.0)
        self._headers = {"User-Agent": user_agent}
        self._cache: dict[str, tuple[float, float] | None] = {}

    def close(self) -> None:
        self._client.close()

    def geocode(self, query: str) -> tuple[float, float]:
        """Return (lat, lon) for a place name or "lat,lon" pair (USA only)."""
        text = query.strip()
        pair = self._try_parse_latlon(text)
        if pair is not None:
            self._ensure_usa(pair, text)
            return pair

        key = text.casefold()
        if key in self._cache:
            cached = self._cache[key]
            if cached is None:
                raise GeocodeError(f"could not find {text!r} in the USA")
            return cached

        lat, lon = self._fetch(text)
        self._cache[key] = (lat, lon)
        return lat, lon

    @staticmethod
    def _try_parse_latlon(text: str) -> tuple[float, float] | None:
        parts = text.replace(";", ",").split(",")
        if len(parts) != 2:
            return None
        try:
            lat, lon = float(parts[0]), float(parts[1])
        except ValueError:
            return None
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            return lat, lon
        return None

    def _fetch(self, text: str) -> tuple[float, float]:
        try:
            resp = self._client.get(
                self._url,
                params={
                    "q": text,
                    "countrycodes": "us",
                    "format": "json",
                    "limit": 1,
                    "addressdetails": 0,
                },
                headers=self._headers,
            )
            resp.raise_for_status()
            results = resp.json()
        except httpx.HTTPError as exc:
            raise GeocodeError(f"geocoding service unreachable: {exc}") from exc
        if not results:
            raise GeocodeError(f"could not find {text!r} in the USA")
        return float(results[0]["lat"]), float(results[0]["lon"])

    @staticmethod
    def _ensure_usa(pair: tuple[float, float], text: str) -> None:
        lat, lon = pair
        if not (US_BOUNDS["min_lat"] <= lat <= US_BOUNDS["max_lat"]
                and US_BOUNDS["min_lon"] <= lon <= US_BOUNDS["max_lon"]):
            raise GeocodeError(f"{text!r} is outside the USA service area")

    @staticmethod
    def distance_between_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
        return haversine_miles(a[0], a[1], b[0], b[1])
