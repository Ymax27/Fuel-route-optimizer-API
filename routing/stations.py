"""Fuel-station store: spatial index + along-route corridor selection.

All stations are loaded once at boot into numpy arrays and a scipy KDTree
(3D unit-sphere coordinates, so antimeridian/pole edge cases are handled).
Per request, we only do:

  1. one ball query around route sample points (microseconds), and
  2. one vectorized projection of the hits onto the route polyline,

which yields each candidate station's position along the route and its
perpendicular distance to it - no extra routing calls.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from routing.geo import EARTH_RADIUS_MILES, locate_on_polyline

STATION_FIELDS = ("opis_id", "name", "city", "state", "lat", "lon", "price_per_gallon")


class StationDataError(RuntimeError):
    """Raised when the enriched station dataset is missing or malformed."""


@dataclass(frozen=True)
class Station:
    opis_id: str
    name: str
    city: str
    state: str
    lat: float
    lon: float
    price_per_gallon: float


@dataclass(frozen=True)
class CorridorStation:
    station: Station
    along_miles: float
    offset_miles: float


def load_stations(csv_path: Path) -> list[Station]:
    """Parse the enriched CSV; raise a clear error if it is unusable."""
    if not csv_path.exists():
        raise StationDataError(
            f"station dataset not found at {csv_path} - run "
            f"'python scripts/enrich_stations.py' first (see README)"
        )
    stations: list[Station] = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [f for f in STATION_FIELDS if f not in (reader.fieldnames or [])]
        if missing:
            raise StationDataError(
                f"station dataset at {csv_path} is missing columns: {missing}"
            )
        for row in reader:
            try:
                stations.append(Station(
                    opis_id=row["opis_id"].strip(),
                    name=row["name"].strip(),
                    city=row["city"].strip(),
                    state=row["state"].strip().upper(),
                    lat=float(row["lat"]),
                    lon=float(row["lon"]),
                    price_per_gallon=float(row["price_per_gallon"]),
                ))
            except (TypeError, ValueError) as exc:
                raise StationDataError(
                    f"malformed row for OPIS id {row.get('opis_id', '?')!r}: {exc}"
                ) from exc
    if not stations:
        raise StationDataError(f"station dataset at {csv_path} is empty")
    return stations


class StationStore:
    """Immutable, process-wide store; cheap to build, O(log n) queries."""

    def __init__(self, stations: list[Station]) -> None:
        self.stations = stations
        self.lats = np.array([s.lat for s in stations], dtype=float)
        self.lons = np.array([s.lon for s in stations], dtype=float)
        self._tree = cKDTree(self._to_xyz(self.lats, self.lons))

    @property
    def size(self) -> int:
        return len(self.stations)

    @staticmethod
    def _to_xyz(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
        phi = np.radians(lats)
        lmb = np.radians(lons)
        return np.column_stack([
            np.cos(phi) * np.cos(lmb),
            np.cos(phi) * np.sin(lmb),
            np.sin(phi),
        ])

    def corridor_candidates(
        self,
        coords_lonlat: np.ndarray,
        cum_miles: np.ndarray,
        radius_miles: float,
        max_per_window: int,
        window_miles: float = 0.5,
    ) -> list[CorridorStation]:
        """Stations within ``radius_miles`` of the route, ordered along it.

        Sample points are taken every ~1.5 miles along the polyline for the
        ball query; hits are then projected exactly onto the polyline and
        filtered by their true perpendicular offset. Within each
        ``window_miles`` slice along the route only the ``max_per_window``
        cheapest stations are kept - stations that share a location can
        only be dominated by cheaper ones at the same spot.
        """
        total = float(cum_miles[-1])
        if total <= 0:
            return []

        # Query points every ~1.5 miles, interpolated ALONG the polyline
        # (snapping to vertices would miss stations between sparse vertices).
        step = 1.5
        n_points = max(int(total / step) + 1, 2)
        target_alongs = np.linspace(0.0, total, n_points)
        j = np.clip(np.searchsorted(cum_miles, target_alongs, side="right") - 1,
                    0, len(cum_miles) - 2)
        seg_len = np.maximum(cum_miles[j + 1] - cum_miles[j], 1e-12)
        t = np.clip((target_alongs - cum_miles[j]) / seg_len, 0.0, 1.0)
        q_lon = coords_lonlat[j, 0] + t * (coords_lonlat[j + 1, 0] - coords_lonlat[j, 0])
        q_lat = coords_lonlat[j, 1] + t * (coords_lonlat[j + 1, 1] - coords_lonlat[j, 1])

        radius_radians = radius_miles / EARTH_RADIUS_MILES
        query_xyz = self._to_xyz(q_lat, q_lon)
        hit_sets = self._tree.query_ball_point(query_xyz, r=radius_radians)
        if hit_sets is None or len(hit_sets) == 0:
            return []

        # (station, sample point) pairs; keep each station's earliest sample.
        pairs: list[tuple[int, int]] = []
        for qi, hits in enumerate(hit_sets):
            for si in hits:
                pairs.append((int(si), qi))
        if not pairs:
            return []
        pairs.sort()
        station_samples: list[tuple[int, int]] = []
        seen: set[int] = set()
        for si, qi in pairs:
            if si not in seen:
                seen.add(si)
                station_samples.append((si, qi))

        # Windowed projection: each station is projected only onto the
        # segments near its sample point, not onto the whole polyline.
        margin = radius_miles + float(np.max(np.diff(cum_miles))) + 1.0
        candidates: list[CorridorStation] = []
        for si, qi in station_samples:
            along_q = float(target_alongs[qi])
            lo = max(int(np.searchsorted(cum_miles, along_q - margin, side="left")) - 1, 0)
            hi = min(int(np.searchsorted(cum_miles, along_q + margin, side="right")) + 1,
                     len(cum_miles) - 1)
            if hi <= lo:
                continue
            along, offset = locate_on_polyline(
                self.lats[si], self.lons[si],
                coords_lonlat[lo:hi + 1], cum_miles[lo:hi + 1],
            )
            if offset <= radius_miles and 0.0 < along < total - 0.1:
                candidates.append(CorridorStation(
                    station=self.stations[si],
                    along_miles=along,
                    offset_miles=offset,
                ))
        candidates.sort(key=lambda c: c.along_miles)

        # Window pruning: identical spots are dominated by cheaper stations.
        pruned: list[CorridorStation] = []
        window_start = 0.0
        i = 0
        n = len(candidates)
        while i < n:
            j = i
            while j < n and candidates[j].along_miles < window_start + window_miles:
                j += 1
            window = sorted(candidates[i:j], key=lambda c: c.station.price_per_gallon)
            pruned.extend(window[:max_per_window])
            window_start += window_miles
            i = j
        return pruned
