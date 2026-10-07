"""Response schemas (Pydantic via django-ninja)."""

from __future__ import annotations

from ninja import Schema


class QueryOut(Schema):
    start: str
    finish: str


class StationOut(Schema):
    opis_id: str
    name: str
    city: str
    state: str
    lat: float
    lon: float
    price_per_gallon: float


class FuelStopOut(Schema):
    order: int
    station: StationOut
    miles_from_start: float
    detour_miles: float
    gallons_to_buy: float
    cost_usd: float


class FuelPlanOut(Schema):
    total_gallons: float
    total_cost_usd: float
    total_stop_detour_miles: float
    stops: list[FuelStopOut]


class RouteOut(Schema):
    distance_miles: float
    duration_hours: float
    start_coords: tuple[float, float]
    finish_coords: tuple[float, float]
    geojson: dict


class MapOut(Schema):
    static_url: str | None
    attribution: str
    interactive_url: str
    legs_geojson: dict


class RouteResponse(Schema):
    request_id: str
    query: QueryOut
    vehicle: dict
    route: RouteOut
    fuel_plan: FuelPlanOut
    map: MapOut
    corridor_candidate_count: int
    timing_ms: dict[str, float]
    cached: bool = False
