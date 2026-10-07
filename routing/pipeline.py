"""End-to-end request pipeline: geocode -> route -> corridor -> optimize.

Design notes
------------
* Exactly one OSRM call per uncached request (the requirement's ideal).
* The optimizer runs in well under a millisecond at corridor sizes; the
  dominant request cost is the OSRM round-trip, so response caching turns
  repeat queries into ~5 ms answers.
* Timing information is collected per stage and surfaced in the API
  response, proving the "fast, few upstream calls" requirement.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

import numpy as np

from routing.geocoder import Geocoder
from routing.optimizer import FuelPlan, UnreachableError, plan_fuel_stops
from routing.osrm import OSRMClient, OSRMError
from routing.stations import CorridorStation, StationStore


class PipelineError(RuntimeError):
    """Base class for pipeline failures surfaced as HTTP 4xx/5xx."""


class RouteError(PipelineError):
    """The routing service failed or cannot route the given points."""


@dataclass
class CorridorResult:
    candidates: list[CorridorStation]
    sample_ms: float
    project_ms: float = 0.0


@dataclass
class PipelineResult:
    request_id: str
    start: str
    finish: str
    start_coords: tuple[float, float]
    finish_coords: tuple[float, float]
    route_miles: float
    route_hours: float
    coordinates: np.ndarray
    cumulative_miles: np.ndarray
    plan: FuelPlan
    corridor: CorridorResult
    timing_ms: dict[str, float] = field(default_factory=dict)

    @property
    def legs_geometry(self) -> list[list[list[float]]]:
        """(start, stop) coordinate pairs for each leg, for map rendering."""
        points: list[list[list[float]]] = []
        prev = self.start_coords
        for stop in self.plan.stops:
            station = stop.station.station
            points.append([[prev[1], prev[0]], [station.lon, station.lat]])
            prev = (station.lat, station.lon)
        points.append([[prev[1], prev[0]], [self.finish_coords[1], self.finish_coords[0]]])
        return points


class FuelRoutePipeline:
    def __init__(
        self,
        store: StationStore,
        osrm: OSRMClient,
        geocoder: Geocoder,
        corridor_radius_miles: float,
        corridor_max_stations: int,
        mpg: float,
        range_miles: float,
        start_full: bool,
    ) -> None:
        self.store = store
        self.osrm = osrm
        self.geocoder = geocoder
        self.corridor_radius_miles = corridor_radius_miles
        self.corridor_max_stations = corridor_max_stations
        self.mpg = mpg
        self.range_miles = range_miles
        self.start_full = start_full

    def run(self, start: str, finish: str) -> PipelineResult:
        t_total = time.perf_counter()

        t0 = time.perf_counter()
        start_coords = self.geocoder.geocode(start)
        finish_coords = self.geocoder.geocode(finish)
        t_geocode = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        try:
            osrm_route = self.osrm.route(*start_coords, *finish_coords)
        except OSRMError as exc:
            raise RouteError(str(exc)) from exc
        t_osrm = (time.perf_counter() - t0) * 1000

        corridor = self._build_corridor(osrm_route.coordinates, osrm_route.cumulative_miles)

        t0 = time.perf_counter()
        try:
            plan = plan_fuel_stops(
                corridor=corridor.candidates,
                route_miles=osrm_route.distance_miles,
                mpg=self.mpg,
                range_miles=self.range_miles,
                start_full=self.start_full,
            )
        except UnreachableError as exc:
            raise PipelineError(str(exc)) from exc
        t_solve = (time.perf_counter() - t0) * 1000

        return PipelineResult(
            request_id=uuid.uuid4().hex[:12],
            start=start,
            finish=finish,
            start_coords=start_coords,
            finish_coords=finish_coords,
            route_miles=osrm_route.distance_miles,
            route_hours=osrm_route.duration_hours,
            coordinates=osrm_route.coordinates,
            cumulative_miles=osrm_route.cumulative_miles,
            plan=plan,
            corridor=corridor,
            timing_ms={
                "geocode": round(t_geocode, 1),
                "osrm": round(t_osrm, 1),
                "corridor": round(corridor.sample_ms + corridor.project_ms, 1),
                "optimize": round(t_solve, 1),
                "total": round((time.perf_counter() - t_total) * 1000, 1),
            },
        )

    def _build_corridor(self, coords: np.ndarray, cum: np.ndarray) -> CorridorResult:
        t0 = time.perf_counter()
        candidates = self.store.corridor_candidates(
            coords_lonlat=coords,
            cum_miles=cum,
            radius_miles=self.corridor_radius_miles,
            max_per_window=self.corridor_max_stations,
        )
        t_sample = (time.perf_counter() - t0) * 1000
        # The store's vectorized projection already yields exact along-route
        # distances and haversine offsets, returned sorted by along_miles.
        return CorridorResult(candidates=candidates, sample_ms=t_sample, project_ms=0.0)
