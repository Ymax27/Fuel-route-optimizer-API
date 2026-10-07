"""Geometry helpers: great-circle distances and along-route projections.

Conventions
-----------
* Coordinates are ``(lon, lat)`` pairs in degrees (GeoJSON order).
* Polyline positions are expressed as ``along`` miles from the route start
  plus a perpendicular ``offset`` in miles.

The projection uses a local equirectangular approximation per segment,
which is accurate to well under a percent at road-segment scales.
"""

from __future__ import annotations

import math

import numpy as np

EARTH_RADIUS_MILES = 3958.7613
MILES_PER_METER = 1.0 / 1609.344
MILES_PER_DEG_LAT = 68.947  # mean meridional degree


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two points (degrees)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def haversine_miles_vec(lat1: np.ndarray, lon1: np.ndarray,
                        lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    """Vectorized haversine in miles (degrees in, miles out)."""
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlmb = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def cumulative_miles(coords_lonlat: np.ndarray, segment_miles: np.ndarray) -> np.ndarray:
    """Cumulative distance array for a polyline: result[0] == 0."""
    return np.concatenate([[0.0], np.cumsum(segment_miles)])


def locate_on_polyline(lat: float, lon: float, coords_lonlat: np.ndarray,
                       cum_miles: np.ndarray) -> tuple[float, float]:
    """Project a point onto a polyline.

    Returns ``(along_miles, offset_miles)`` for the closest point on the
    polyline. ``offset_miles`` is the perpendicular distance to the route.
    """
    lons = coords_lonlat[:, 0]
    lats = coords_lonlat[:, 1]
    x1, y1 = lons[:-1], lats[:-1]   # segment starts
    x2, y2 = lons[1:], lats[1:]     # segment ends

    lat_mid = 0.5 * (y1 + y2)
    kx = np.cos(np.radians(lat_mid)) * MILES_PER_DEG_LAT

    # Local plane vectors (miles), per segment.
    seg_dx = (x2 - x1) * kx
    seg_dy = (y2 - y1) * MILES_PER_DEG_LAT
    seg_len_sq = seg_dx * seg_dx + seg_dy * seg_dy
    seg_len_sq = np.where(seg_len_sq == 0.0, 1e-12, seg_len_sq)

    px = (lon - x1) * kx      # point relative to segment start
    py = (lat - y1) * MILES_PER_DEG_LAT

    t = np.clip((px * seg_dx + py * seg_dy) / seg_len_sq, 0.0, 1.0)
    off_x = px - t * seg_dx
    off_y = py - t * seg_dy
    off_sq = off_x * off_x + off_y * off_y

    best = int(np.argmin(off_sq))
    seg_miles = np.hypot(seg_dx, seg_dy)
    along = float(cum_miles[best] + t[best] * seg_miles[best])
    offset = float(math.sqrt(off_sq[best]))
    return along, offset


def bounds_deg(coords_lonlat: np.ndarray) -> tuple[float, float, float, float]:
    """Return (min_lon, min_lat, max_lon, max_lat) of a coordinate array."""
    lons, lats = coords_lonlat[:, 0], coords_lonlat[:, 1]
    return float(lons.min()), float(lats.min()), float(lons.max()), float(lats.max())
