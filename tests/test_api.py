"""API-level tests using Django's test client with the fake pipeline."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import Client

from api import container


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_client(pipeline, monkeypatch) -> Client:
    monkeypatch.setattr(container, "_pipeline", pipeline)
    return Client()


class TestRouteEndpoint:
    def test_returns_full_payload(self, api_client):
        resp = api_client.get("/api/route", {"start": "Wichita, KS", "finish": "Salina, KS"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == {"start": "Wichita, KS", "finish": "Salina, KS"}
        assert data["route"]["distance_miles"] == pytest.approx(207, abs=5)
        assert data["vehicle"]["range_miles"] == 500.0
        assert data["vehicle"]["mpg"] == 10.0
        assert "fuel_plan" in data and "total_cost_usd" in data["fuel_plan"]
        assert data["map"]["static_url"] and "staticmap" in data["map"]["static_url"]
        assert data["map"]["interactive_url"].startswith("/map/")
        assert data["timing_ms"]["osrm"] >= 0
        assert data["cached"] is False
        assert data["corridor_candidate_count"] >= 1

    def test_second_request_is_cached(self, api_client):
        first = api_client.get("/api/route", {"start": "Wichita, KS", "finish": "Salina, KS"})
        second = api_client.get("/api/route", {"start": "Wichita, KS", "finish": "Salina, KS"})
        assert first.status_code == second.status_code == 200
        assert second.json()["cached"] is True

    def test_unknown_place_returns_400(self, api_client):
        resp = api_client.get("/api/route", {"start": "Nowhere, ZZ", "finish": "Salina, KS"})
        assert resp.status_code == 400

    def test_missing_params_return_422(self, api_client):
        resp = api_client.get("/api/route", {"start": "Wichita, KS"})
        assert resp.status_code == 422


class TestHealth:
    def test_health_reports_dataset(self, api_client):
        resp = api_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["stations_indexed"] == 5
        assert data["vehicle"]["mpg"] == 10.0
