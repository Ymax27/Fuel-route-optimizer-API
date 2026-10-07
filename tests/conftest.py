"""Shared fixtures: synthetic store + fakes, so no test touches the network."""

from __future__ import annotations

import numpy as np
import pytest

from routing.geocoder import GeocodeError
from routing.osrm import OSRMRoute
from routing.pipeline import FuelRoutePipeline
from routing.stations import Station, StationStore


class FakeOSRM:
    """Deterministic straight-line route along a meridian."""

    def __init__(self, start_lat: float = 38.0, end_lat: float = 41.0,
                 lon: float = -98.0, speed_mph: float = 60.0) -> None:
        self.start_lat, self.end_lat, self.lon = start_lat, end_lat, lon
        self.speed_mph = speed_mph
        self.calls = 0

    def route(self, start_lat: float, start_lon: float,
              finish_lat: float, finish_lon: float) -> OSRMRoute:
        self.calls += 1
        lats = np.linspace(self.start_lat, self.end_lat, 50)
        coords = np.column_stack([np.full_like(lats, self.lon), lats])
        # ~69 miles per degree of latitude
        cumulative = (lats - lats[0]) * 69.0
        return OSRMRoute(
            distance_miles=float(cumulative[-1]),
            duration_hours=float(cumulative[-1]) / self.speed_mph,
            coordinates=coords,
            cumulative_miles=cumulative,
        )


class FakeGeocoder:
    def __init__(self, mapping: dict[str, tuple[float, float]]) -> None:
        self.mapping = mapping

    def geocode(self, query: str) -> tuple[float, float]:
        q = query.strip().casefold()
        if q in self.mapping:
            return self.mapping[q]
        raise GeocodeError(f"could not find {query!r} in the USA")


def make_store(stations: list[Station]) -> StationStore:
    return StationStore(stations)


@pytest.fixture
def kansas_store() -> StationStore:
    """Stations every ~69 miles along I-135-ish corridor with varied prices."""
    lats_prices = [
        (38.2, 3.50), (38.9, 2.80), (39.6, 3.10), (40.3, 2.60), (40.9, 3.40),
    ]
    return line_store(lats_prices)


def line_store(lats_prices: list[tuple[float, float]]) -> StationStore:
    return StationStore([
        Station(opis_id=str(i), name=f"S{i}", city="X", state="KS",
                lat=lat, lon=-98.0, price_per_gallon=price)
        for i, (lat, price) in enumerate(lats_prices)
    ])


@pytest.fixture
def pipeline(kansas_store) -> FuelRoutePipeline:
    geocoder = FakeGeocoder({
        "wichita, ks": (38.0, -98.0),
        "salina, ks": (41.0, -98.0),
    })
    return FuelRoutePipeline(
        store=kansas_store,
        osrm=FakeOSRM(start_lat=38.0, end_lat=41.0),
        geocoder=geocoder,
        corridor_radius_miles=15.0,
        corridor_max_stations=40,
        mpg=10.0,
        range_miles=500.0,
        start_full=True,
    )
