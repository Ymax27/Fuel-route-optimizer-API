"""Static map URL builder (OpenStreetMap staticmap service, free & keyless).

The URL embeds start/finish/fuel-stop markers so the JSON response carries a
ready-to-display map image without any client-side work.
"""

from __future__ import annotations

import math
from urllib.parse import quote

STATICMAP_URL = "https://staticmap.openstreetmap.de/staticmap.php"
ATTRIBUTION = "map data (c) OpenStreetMap contributors"
_MAX_MARKERS = 24
_MAX_URL_LEN = 2000


def build_static_map_url(
    coords_lonlat,
    start: tuple[float, float],
    finish: tuple[float, float],
    stops: list[tuple[float, float]],
    size: str = "800x500",
) -> str | None:
    """Return a static map URL, or None if the geometry is unusable."""
    coords = list(coords_lonlat)
    if len(coords) < 2:
        return None
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)
    center = ((min_lat + max_lat) / 2, (min_lon + max_lon) / 2)

    # Choose a zoom that fits the bounding box in ~800 px of width.
    lon_span = max(max_lon - min_lon, 1e-4)
    zoom = max(3, min(13, math.floor(math.log2(360.0 / lon_span))))

    markers = [
        f"{start[0]:.6f},{start[1]:.6f},green-pushpin",
        f"{finish[0]:.6f},{finish[1]:.6f},red-pushpin",
    ]
    markers += [f"{lat:.6f},{lon:.6f},blue-circle"
                for lat, lon in stops[:_MAX_MARKERS - 2]]

    base = (f"{STATICMAP_URL}?center={center[0]:.6f},{center[1]:.6f}"
            f"&zoom={zoom}&size={size}&maptype=mapnik")
    url = base + "&markers=" + "|".join(quote(m, safe=",|") for m in markers)
    if len(url) > _MAX_URL_LEN:  # keep URLs sane; drop extra stop markers
        url = base + "&markers=" + "|".join(markers[:6])
    return url
