"""Assemble a PipelineResult into the API response payload."""

from __future__ import annotations

import numpy as np

from api.schemas import (
    FuelPlanOut,
    FuelStopOut,
    MapOut,
    QueryOut,
    RouteOut,
    RouteResponse,
    StationOut,
)
from routing.pipeline import PipelineResult

_VEHICLE_INFO = {"range_miles": 500.0, "mpg": 10.0}


def build_response(result: PipelineResult, vehicle: dict | None = None,
                   static_url: str | None = None) -> RouteResponse:
    vehicle = vehicle or dict(_VEHICLE_INFO)

    stops = [
        FuelStopOut(
            order=i,
            station=StationOut(
                opis_id=s.station.station.opis_id,
                name=s.station.station.name,
                city=s.station.station.city,
                state=s.station.station.state,
                lat=s.station.station.lat,
                lon=s.station.station.lon,
                price_per_gallon=round(s.station.station.price_per_gallon, 3),
            ),
            miles_from_start=round(s.station.along_miles, 1),
            detour_miles=round(s.station.offset_miles, 1),
            gallons_to_buy=round(s.gallons, 2),
            cost_usd=round(s.cost_usd, 2),
        )
        for i, s in enumerate(result.plan.stops, start=1)
    ]

    plan_out = FuelPlanOut(
        total_gallons=round(result.plan.total_gallons, 2),
        total_cost_usd=round(result.plan.total_cost, 2),
        total_stop_detour_miles=round(result.plan.total_stop_distance_miles, 1),
        stops=stops,
    )

    route_geojson = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": np.round(result.coordinates, 5).tolist(),
        },
        "properties": {
            "distance_miles": round(result.route_miles, 1),
            "duration_hours": round(result.route_hours, 2),
        },
    }

    legs_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": leg},
                "properties": {"leg": i},
            }
            for i, leg in enumerate(result.legs_geometry, start=1)
        ],
    }

    return RouteResponse(
        request_id=result.request_id,
        query=QueryOut(start=result.start, finish=result.finish),
        vehicle=vehicle,
        route=RouteOut(
            distance_miles=round(result.route_miles, 1),
            duration_hours=round(result.route_hours, 2),
            start_coords=result.start_coords,
            finish_coords=result.finish_coords,
            geojson=route_geojson,
        ),
        fuel_plan=plan_out,
        map=MapOut(
            static_url=static_url,
            attribution="map data (c) OpenStreetMap contributors",
            interactive_url=f"/map/{result.request_id}",
            legs_geojson=legs_geojson,
        ),
        corridor_candidate_count=len(result.corridor.candidates),
        timing_ms=result.timing_ms,
    )
