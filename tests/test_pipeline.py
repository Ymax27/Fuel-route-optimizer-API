"""Pipeline tests with fakes - exercises the full flow without network."""

from __future__ import annotations

import pytest
from conftest import FakeGeocoder, FakeOSRM

from routing.geocoder import GeocodeError
from routing.pipeline import FuelRoutePipeline, PipelineError


class TestHappyPath:
    def test_full_pipeline_produces_plan(self, pipeline):
        result = pipeline.run("Wichita, KS", "Salina, KS")
        assert result.route_miles == pytest.approx(207.0, abs=3.0)
        assert result.timing_ms["total"] > 0
        assert result.corridor.candidates, "corridor must find the line stations"

    def test_osrm_called_once(self, pipeline):
        pipeline.run("Wichita, KS", "Salina, KS")
        assert pipeline.osrm.calls == 1

    def test_short_route_needs_no_stops(self, pipeline):
        result = pipeline.run("Wichita, KS", "Salina, KS")
        assert result.plan.stops == []
        assert result.plan.total_cost == 0.0

    def test_legs_geometry_chains_stops(self, pipeline):
        result = pipeline.run("Wichita, KS", "Salina, KS")
        n_stops = len(result.plan.stops)
        assert len(result.legs_geometry) == n_stops + 1


class TestErrors:
    def test_unknown_location_raises_geocode_error(self, pipeline):
        with pytest.raises(GeocodeError):
            pipeline.run("Nowhere, ZZ", "Salina, KS")

    def test_long_route_without_reachable_fuel_raises(self, kansas_store):
        geocoder = FakeGeocoder({"a": (20.0, -98.0), "b": (45.0, -98.0)})
        pl = FuelRoutePipeline(
            store=kansas_store, osrm=FakeOSRM(start_lat=20.0, end_lat=45.0),
            geocoder=geocoder, corridor_radius_miles=0.001,
            corridor_max_stations=1, mpg=10.0, range_miles=100.0, start_full=True,
        )
        with pytest.raises(PipelineError):
            pl.run("a", "b")
