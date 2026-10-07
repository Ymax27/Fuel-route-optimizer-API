"""Build a US city gazetteer from the GeoNames cities500 dump.

Source: https://download.geonames.org/export/dump/cities500.zip (CC-BY 4.0)

The gazetteer maps (city, state) -> (lat, lon, population) and is used to
geocode the fuel-station CSV offline, so no runtime geocoding is required.

Usage:
    python scripts/build_gazetteer.py <path/to/cities500.txt>

Output: data/us_cities.csv with columns city,state,lat,lon,population
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "data" / "us_cities.csv"

# GeoNames cities500 column indexes (tab separated).
COL_NAME = 1
COL_LAT = 4
COL_LON = 5
COL_COUNTRY = 8
COL_ADMIN1 = 10  # US state code, e.g. "OH"
COL_POPULATION = 14


def main(dump_path: str) -> int:
    cities: dict[tuple[str, str], tuple[float, float, int]] = {}
    by_state: dict[str, int] = defaultdict(int)

    with open(dump_path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(row) <= COL_POPULATION or row[COL_COUNTRY] != "US":
                continue
            state = row[COL_ADMIN1].strip()
            city = row[COL_NAME].strip()
            if not city or len(state) != 2:
                continue
            try:
                lat = float(row[COL_LAT])
                lon = float(row[COL_LON])
                population = int(row[COL_POPULATION] or 0)
            except ValueError:
                continue
            key = (city.casefold(), state)
            # Keep the most populous record when several entries share a name.
            if key not in cities or population > cities[key][2]:
                cities[key] = (lat, lon, population)
            by_state[state] += 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["city", "state", "lat", "lon", "population"])
        for (city, state), (lat, lon, population) in sorted(cities.items()):
            writer.writerow([city, state, f"{lat:.6f}", f"{lon:.6f}", population])

    print(f"Wrote {len(cities):,} US cities to {OUTPUT_PATH}")
    print(f"States/territories covered: {len(by_state)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/build_gazetteer.py <cities500.txt>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
