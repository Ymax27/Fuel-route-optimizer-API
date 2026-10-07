# Fuel Route Optimizer API

Given any start and finish location in the USA, this Django API returns the driving
route, the **cost-optimal fuel stops** for a vehicle with a 500-mile range and 10 mpg,
and the **total fuel spend** - computed by exact dynamic programming over ~6,000
fuel stations, with a single call to the free OSRM routing service.

> Example: *San Francisco → New York* = 2,909.8 mi, **$734.17** in fuel,
> 14 refuel stops (242.58 gal), first call ~4–5 s / cached call ~15 ms.

## Quick start

```bash
./setup.sh                      # creates .venv, installs deps, runs Django checks
source .venv/bin/activate
python manage.py runserver      # API on http://localhost:8000
```

Open **http://localhost:8000/api/docs** for the interactive Swagger UI, or import
`postman_collection.json` into Postman for one-click demo requests.

## API

### `GET /api/route?start=<place>&finish=<place>`

`start` / `finish` accept free-text US places (`"Cincinnati, OH"`) or raw
`"lat,lon"` pairs (skips geocoding entirely).

```json
{
  "request_id": "a2c5c6c5d06a",
  "query": { "start": "Cincinnati, OH", "finish": "Chicago, IL" },
  "vehicle": { "range_miles": 500.0, "mpg": 10.0, "start_with_full_tank": true },
  "route": {
    "distance_miles": 289.5,
    "duration_hours": 5.65,
    "start_coords": [39.1012809, -84.5127405],
    "finish_coords": [41.8755616, -87.6244212],
    "geojson": { "type": "Feature", "geometry": { "type": "LineString", "coordinates": [ ... ] } }
  },
  "fuel_plan": {
    "total_gallons": 0.0,
    "total_cost_usd": 0.0,
    "total_stop_detour_miles": 0.0,
    "stops": []
  },
  "map": {
    "static_url": "https://staticmap.openstreetmap.de/staticmap.php?...",
    "interactive_url": "/map/a2c5c6c5d06a",
    "legs_geojson": { "type": "FeatureCollection", "features": [ ... ] }
  },
  "corridor_candidate_count": 125,
  "timing_ms": { "geocode": 2764.3, "osrm": 1437.1, "corridor": 39.3, "optimize": 3.6, "total": 4244.4 },
  "cached": false
}
```

The 289-mile Cincinnati → Chicago trip needs **zero refuel stops**: the tank
already covers it. On a long haul (San Francisco → New York, 2,909.8 mi) the
same endpoint returns 14 stops, e.g.:

```json
{
  "order": 1,
  "station": { "opis_id": "7873", "name": "One9 #387", "city": "Carlin", "state": "NV",
               "lat": 40.71381, "lon": -116.10397, "price_per_gallon": 3.439 },
  "miles_from_start": 484.0,
  "detour_miles": 0.6,
  "gallons_to_buy": 7.06,
  "cost_usd": 24.28
}
```

Other endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Liveness + dataset stats |
| `GET /api/docs` | Swagger UI (django-ninja) |
| `GET /map/{request_id}` | Interactive Leaflet map of a planned route |

### Error handling

| Status | Meaning |
|---|---|
| `400` | A location could not be geocoded inside the USA |
| `422` | Route is not fuelable: some stretch between reachable stations exceeds the 500-mile range (the error message locates the gap) |
| `502` | The routing service is unreachable |

## Architecture

```
Postman / browser
        │  GET /api/route?start=...&finish=...
        ▼
┌──────────────────── Django 6.1 + django-ninja ────────────────────┐
│  validation (Pydantic) → response cache (keyed by normalized      │
│  start+finish; repeat requests answer in ~15 ms, "cached": true)  │
│        ▼ miss                                                     │
│  1. Nominatim geocoding   ≤ 1 call, only for text inputs, cached  │
│  2. OSRM route            exactly 1 call per uncached request:    │
│                           overview=full + per-node distance       │
│                           annotations → exact cumulative mileage  │
│  3. Station corridor      KDTree (scipy) over the station dataset │
│                           loaded once at boot; stations within    │
│                           15 mi of the route, projected onto it   │
│  4. DP optimizer          100 % local, < 1 ms, provably optimal   │
│  5. Response assembly     GeoJSON, costs, static map URL, timings │
└───────────────────────────────────────────────────────────────────┘
```

Code layout - the business logic (`routing/`) has **zero Django imports** and is
unit-testable on its own; `api/` is a thin HTTP shell around it:

```
config/          Django settings (env-driven, lean, no ORM models)
api/             HTTP layer: ninja endpoints, Pydantic schemas, Leaflet page
routing/         business logic:
  geo.py           haversine, along-route projection math
  osrm.py          single-call OSRM client with exact distance annotations
  geocoder.py      Nominatim client (cached, US-bounds validated)
  stations.py      station store: KDTree + corridor selection
  optimizer.py     the DP fuel-stop planner
  pipeline.py      orchestration + per-stage timings
scripts/         reproducible dataset build (gazetteer + geocoding)
data/            fuel prices, gazetteer, enriched stations
tests/           31 tests - no network access
```

## The algorithm

Total consumption for a trip is fixed at `distance / mpg` gallons, so the only
cost lever is **where** each gallon is bought. Model the stations along the
route (sorted by mile position) as nodes in a graph: an edge `j → i` exists if
the leg fits in one tank (`mile_i − mile_j ≤ 500`) and costs
`(mile_i − mile_j) / mpg × price_j` - the fuel for a leg is bought at its
*source* station. A virtual start node with price 0 represents the free initial
full tank; the finish is a sink.

The cheapest plan is then a shortest path, computed with the recurrence
(`best[k] = min over reachable j of best[j] + leg_cost(j, k)`), which is exact -
not a heuristic. Per-stop purchase amounts follow from the chosen path: each
stop pays for the fuel burned after it until the next purchase point.

Two properties make this robust in practice:

* **Optimality** - a greedy heuristic ("fill up at the cheapest visible station")
  can be beaten when several stations share a window; the DP is provably optimal
  for the corridor model. `tests/test_optimizer.py` encodes scenarios where the
  naive strategy overpays, including the case where skipping a *cheaper-but-early*
  station is optimal.
* **Tie-breaking** - among equal-cost plans we prefer the ones with the smallest
  off-route detour (a `1e-3 $/mile` priority term), so the map stays clean and
  the truck never leaves the highway for a tied price.

Complexity: the DP scans all earlier nodes for each candidate (`O(n²)` cheap
float comparisons, `n` = corridor stations, typically a few hundred), of which
only `O(n · k)` edges are feasible (`k` = stations within one tank). Measured
~25–30 ms for the 544-candidate San Francisco → New York corridor.

### Assumptions (explicit, configurable)

| Assumption | Default | Notes |
|---|---|---|
| Tank is full at departure | yes (`FUEL_START_FULL`) | The first 500 miles are already paid; if disabled, the first stop prices the pre-station miles at its own pump price. |
| Refuel amounts are continuous | yes | Gallons can be fractional (truckers pre-authorize by dollar amount anyway). |
| Station coordinates are city centroids | yes | The provided CSV has no coordinates; stations were geocoded offline to city level (see below). Typical error is a few miles. |
| Equal-cost plans avoid detours | yes | Fuel cost only depends on where gallons are bought; among **tied** cheapest plans the DP prefers stops closest to the highway (`1e-3 $/mile` priority term), so the truck never leaves the road for a tied price. Detour miles are reported but not billed. |
| Corridor width | 15 mi | Stations farther from the route are ignored; configurable via `CORRIDOR_RADIUS_MILES`. |
| Canadian stations are excluded | yes | The CSV contains some; the exercise scopes to the USA. |

## Performance

Measured on a laptop against the public OSRM demo server (San Francisco →
New York, 2,909 mi):

| Scenario | Latency |
|---|---|
| First request (geocode + OSRM + solve) | ~0.6–4 s (upstream network dominates) |
| Repeat request (cached) | ~15 ms, `"cached": true` |
| Corridor search + projection alone | ~60 ms for a 2,900-mile route |
| Optimization stage alone | ~24 ms |

The routing budget is exactly **one** OSRM call per uncached request (the
exercise's stated ideal). Everything else - corridor search, projection,
optimization - is local numpy/scipy work on data loaded once at boot.
The per-stage `timing_ms` block in every response makes this verifiable.

## The dataset pipeline

The assessment CSV (`data/fuel-prices-for-be-assessment.csv`) has 8,151 rows,
6,738 unique OPIS station IDs - and **no coordinates**. Geocoding 6,000+
stations at runtime would violate both the speed and the "don't hammer free
APIs" requirements, so coordinates are resolved **once, offline**, and the
result ships with the repo:

```bash
python scripts/build_gazetteer.py cities500.txt    # optional: rebuild gazetteer
python scripts/enrich_stations.py                  # offline match (gazetteer + fuzzy)
python scripts/enrich_stations.py --online         # + rate-limited Nominatim fallback
```

1. **Gazetteer** - 21,457 US cities with coordinates, derived from the GeoNames
   `cities500` dump (CC-BY 4.0) - see `data/us_cities.csv`.
2. **Exact match** on ASCII-folded `(city, state)` → covers ~91 % of stations.
3. **Fuzzy same-state match** (difflib ≥ 0.92, unique best) for small towns.
4. **Nominatim fallback** for the rest, rate-limited to 1 req/s and cached in
   `data/geocode_cache.json`, so the API itself never geocodes stations.

Rows are deduplicated by OPIS ID keeping the **lowest observed retail price**,
and non-US stations are dropped. Result: `data/stations_enriched.csv`,
**6,611 stations (98.6 % coverage)** with coordinates, loaded into a scipy
KDTree at boot (~0.2 s).

## Testing & CI

```bash
pytest --cov=routing --cov=api        # 31 tests, no network
ruff check .                          # lint clean
```

The suite covers the optimizer (optimality, greedy traps, unreachable gaps,
tank-start variants), corridor projection math, dataset parsing errors and the
HTTP layer (payload contract, caching flag, 400/422 handling) - all against
fakes, so CI never touches the network. GitHub Actions runs lint + checks +
tests on every push (`.github/workflows/ci.yml`).

## Configuration

All settings are environment variables with sensible defaults - see
`.env.example`. Notably `OSRM_BASE_URL` can point at a self-hosted OSRM
(docker-compose ships a commented block for it) for production use without
demo-server rate limits.

## Data attribution

* Fuel prices: assessment-provided CSV (OPIS truckstop list).
* City gazetteer: [GeoNames](https://www.geonames.org/) `cities500`, CC-BY 4.0.
* Maps & routing: © [OpenStreetMap](https://www.openstreetmap.org/copyright)
  contributors (OSRM demo server, Nominatim, OSM tiles).
