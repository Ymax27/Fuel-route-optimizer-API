"""Enrich the raw fuel-price CSV with station coordinates.

The assessment CSV has no coordinates, so we geocode stations offline by
matching (city, state) against a GeoNames-derived US gazetteer
(data/us_cities.csv, built by scripts/build_gazetteer.py). This turns the
raw list into a ready-to-use dataset the API loads at boot - no runtime
geocoding, no per-request network calls.

Matching strategy, in order:
  1. exact (city, state) after ASCII folding and whitespace cleanup
  2. high-confidence fuzzy match within the same state (difflib >= 0.92,
     unique best candidate) for small towns missing from the gazetteer
  3. Nominatim lookup, rate-limited and cached to
     data/geocode_cache.json - only used when --online is passed, since
     it is a one-time, offline-friendly build step
  4. stations still unresolved are dropped

US-only: stations in Canadian provinces (e.g. Calgary, AB) are dropped -
the exercise scopes the problem to the USA. Rows are deduped by OPIS
Truckstop ID keeping the lowest retail price (most favorable observed
price per station).

Usage:
    python scripts/enrich_stations.py            # offline (passes 1-2)
    python scripts/enrich_stations.py --online   # passes 1-3

Output: data/stations_enriched.csv
Columns: opis_id,name,city,state,lat,lon,price_per_gallon,source
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_CSV = PROJECT_ROOT / "data" / "fuel-prices-for-be-assessment.csv"
GAZETTEER_CSV = PROJECT_ROOT / "data" / "us_cities.csv"
OUTPUT_CSV = PROJECT_ROOT / "data" / "stations_enriched.csv"
GEOCODE_CACHE = PROJECT_ROOT / "data" / "geocode_cache.json"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_DELAY_S = 1.1  # usage policy: max 1 request per second
NOMINATIM_USER_AGENT = "fuel-route-optimizer-assessment/1.0 (one-time dataset build)"
FUZZY_THRESHOLD = 0.92

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
}

FIELDNAMES = ["opis_id", "name", "city", "state", "lat", "lon", "price_per_gallon", "source"]


def fold(value: str) -> str:
    """Normalize a city name: ASCII-fold, lowercase, collapse spaces."""
    ascii_folded = unicodedata.normalize("NFKD", value)
    ascii_only = ascii_folded.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_only).strip().lower()


def load_gazetteer() -> tuple[dict[tuple[str, str], tuple[float, float]], dict[str, dict[str, tuple[float, float]]]]:
    """Return (exact_index, by_state_index) built from the gazetteer CSV."""
    exact: dict[tuple[str, str], tuple[float, float]] = {}
    by_state: dict[str, dict[str, tuple[float, float]]] = {}
    with open(GAZETTEER_CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            city = fold(row["city"])
            state = row["state"].strip().upper()
            coords = (float(row["lat"]), float(row["lon"]))
            # Earlier rows are more populous (gazetteer is sorted); keep them
            # as the canonical location for a duplicated city name.
            exact.setdefault((city, state), coords)
            by_state.setdefault(state, {}).setdefault(city, coords)
    return exact, by_state


def fuzzy_lookup(by_state: dict[str, dict[str, tuple[float, float]]],
                 city: str, state: str) -> tuple[float, float] | None:
    """Unique high-confidence same-state match, e.g. 'Alexandria' -> 'Alexandria'."""
    candidates = by_state.get(state, {})
    if not candidates:
        return None
    scored = [(difflib.SequenceMatcher(None, city, name).ratio(), name)
              for name in candidates]
    scored.sort(reverse=True)
    if len(scored) >= 2 and abs(scored[0][0] - scored[1][0]) < 1e-9:
        return None  # tie: ambiguous, refuse to guess
    if scored[0][0] >= FUZZY_THRESHOLD:
        return candidates[scored[0][1]]
    return None


def load_geocode_cache() -> dict:
    if GEOCODE_CACHE.exists():
        return json.loads(GEOCODE_CACHE.read_text(encoding="utf-8"))
    return {}


def nominatim_lookup(client: httpx.Client, city: str, state: str) -> tuple[float, float] | None:
    resp = client.get(
        NOMINATIM_URL,
        params={"city": city, "state": state, "country": "USA", "format": "json", "limit": 1},
        headers={"User-Agent": NOMINATIM_USER_AGENT},
        timeout=15,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    return float(results[0]["lat"]), float(results[0]["lon"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich fuel stations with coordinates.")
    parser.add_argument("--online", action="store_true",
                        help="also use the Nominatim fallback (rate-limited, cached)")
    args = parser.parse_args()

    exact, by_state = load_gazetteer()
    cache = load_geocode_cache()
    cache_dirty = False
    client = httpx.Client() if args.online else None

    stations: dict[str, dict] = {}
    unresolved: set[tuple[str, str]] = set()
    stats = {"rows": 0, "us_rows": 0, "gazetteer": 0, "fuzzy": 0, "nominatim": 0}

    with open(RAW_CSV, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            stats["rows"] += 1
            city_raw = re.sub(r"\s+", " ", row["City"]).strip()
            state = row["State"].strip().upper()
            if state not in US_STATES:
                continue  # non-US station (e.g. Canadian provinces)
            stats["us_rows"] += 1
            city = fold(city_raw)

            coords = exact.get((city, state))
            source = "gazetteer"
            if coords is None:
                coords = fuzzy_lookup(by_state, city, state)
                source = "fuzzy"
            if coords is None and client is not None:
                cache_key = f"{city_raw}|{state}"
                if cache_key in cache:
                    entry = cache[cache_key]
                    coords = (entry["lat"], entry["lon"]) if entry else None
                else:
                    time.sleep(NOMINATIM_DELAY_S)
                    try:
                        coords = nominatim_lookup(client, city_raw, state)
                    except Exception as exc:  # noqa: BLE001 - keep the build going
                        print(f"nominatim error for {city_raw}, {state}: {exc}", file=sys.stderr)
                        coords = None
                    cache[cache_key] = {"lat": coords[0], "lon": coords[1]} if coords else None
                    # Persist after every lookup so an interrupted run resumes.
                    GEOCODE_CACHE.write_text(json.dumps(cache), encoding="utf-8")
                    cache_dirty = True
                source = "nominatim"
            if coords is None:
                unresolved.add((city_raw, state))
                continue
            stats[source] += 1

            opis_id = row["OPIS Truckstop ID"].strip()
            price = float(row["Retail Price"])
            existing = stations.get(opis_id)
            if existing is None or price < existing["_price"]:
                stations[opis_id] = {
                    "opis_id": opis_id,
                    "name": re.sub(r"\s+", " ", row["Truckstop Name"]).strip(),
                    "city": city_raw,
                    "state": state,
                    "lat": f"{coords[0]:.6f}",
                    "lon": f"{coords[1]:.6f}",
                    "_price": price,
                    "source": source,
                }

    if client is not None:
        client.close()
    if cache_dirty:
        GEOCODE_CACHE.write_text(json.dumps(cache, indent=0), encoding="utf-8")

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for station in sorted(stations.values(), key=lambda s: (s["state"], s["city"], s["name"])):
            writer.writerow({**station, "price_per_gallon": f"{station['_price']:.6f}"})

    print(f"Rows read:            {stats['rows']:,}")
    print(f"US rows:              {stats['us_rows']:,}")
    print(f"  exact gazetteer:    {stats['gazetteer']:,}")
    print(f"  fuzzy same-state:   {stats['fuzzy']:,}")
    print(f"  nominatim fallback: {stats['nominatim']:,}")
    print(f"Unique US stations:   {len(stations):,}")
    print(f"Unresolved city pairs:{len(unresolved)}")
    if unresolved:
        print("  examples:", sorted(unresolved)[:15])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
