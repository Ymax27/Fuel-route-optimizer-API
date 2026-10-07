"""Interactive map page for visualizing a planned route."""

from __future__ import annotations

import json

from django.core.cache import cache
from django.http import Http404
from django.shortcuts import render


def map_page(request, request_id: str):
    payload = _find_cached_payload(request_id)
    if payload is None:
        raise Http404("unknown or expired request id")

    context = {
        "request_id": request_id,
        "payload": json.dumps(payload),
        "start": payload["query"]["start"],
        "finish": payload["query"]["finish"],
        "total_cost": payload["fuel_plan"]["total_cost_usd"],
        "total_gallons": payload["fuel_plan"]["total_gallons"],
        "distance": payload["route"]["distance_miles"],
        "stops": payload["fuel_plan"]["stops"],
    }
    return render(request, "map.html", context)


def _find_cached_payload(request_id: str):
    # Responses are cached under composite keys; we store an id -> key index
    # to keep lookups O(1). The cached value is the serialized JSON string.
    key_index = cache.get(f"fuelroute:idx:{request_id}")
    if not key_index:
        return None
    payload = cache.get(key_index)
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload
