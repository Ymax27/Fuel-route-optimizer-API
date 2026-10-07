"""Optimal fuel-stop planner.

Model
-----
The vehicle starts with a full tank (50 gallons at 10 mpg, 500-mile range -
both configurable). Total consumption for a route is fixed, so the *only*
cost lever is WHERE each gallon is bought. We therefore solve:

  minimize   sum over stops of (gallons bought at stop) x (price at stop)
  subject to the tank never dropping below empty between consecutive stops

Candidates are sorted by ``along_miles`` (distance along the chosen route).
Let D[i] be the along-route distance of candidate i, and let D0 = -range
be a virtual "start" stop with free fuel (the initial full tank) and D1 the
finish. Because every reachable candidate can top up to full, an optimal
plan is always composed of full-tank-or-smaller refuels that jump forward
at most ``range`` miles, and the shortest-path DP below is exact:

  best[i] = min over reachable j (including virtual start) of
              best[j] + (D[i] - D[j]) / mpg * price[j]

where ``price[start] = 0`` models the free initial tank and ``finish`` uses
price 0 for the final partial segment. ``best[k]`` is +inf when candidate k
is unreachable, which propagates to the finish if the route cannot be
driven at all (fuel gap > range).

Tie-breaking
------------
Multiple plans can share the same optimal cost. We minimize a scalar
objective ``total_cost + priority_weight * total_stop_distance`` so the
selected plan visits the *closest* stations among equally cheap options -
nicer on a map and fewer detours - without ever paying a cent more.
``priority_weight = 1e-3`` dollars per detour-mile keeps the guarantee
that cost is lexicographically first (two plans differ by at least
half a cent per gallon-step, and detours are bounded by corridor radius).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from routing.stations import CorridorStation


class UnreachableError(ValueError):
    """Fuel gap larger than the vehicle range: the route cannot be driven."""


@dataclass(frozen=True)
class FuelStop:
    station: CorridorStation
    gallons: float
    cost_usd: float


@dataclass(frozen=True)
class FuelPlan:
    stops: list[FuelStop]
    total_cost: float
    total_gallons: float
    total_stop_distance_miles: float


def plan_fuel_stops(
    corridor: list[CorridorStation],
    route_miles: float,
    mpg: float,
    range_miles: float,
    start_full: bool = True,
    priority_weight: float = 1e-3,
) -> FuelPlan:
    """Return the cheapest feasible refuel plan (exact DP).

    ``corridor`` must be sorted by ``along_miles`` ascending. Raises
    :class:`UnreachableError` when some stretch between consecutive
    reachable candidates exceeds the vehicle range.
    """
    if route_miles <= 0:
        return FuelPlan(stops=[], total_cost=0.0, total_gallons=0.0,
                        total_stop_distance_miles=0.0)
    if mpg <= 0 or range_miles <= 0:
        raise ValueError("mpg and range_miles must be positive")

    n = len(corridor)
    if n == 0 and route_miles > range_miles:
        raise UnreachableError(
            f"no candidate stations near the route and route length "
            f"{route_miles:.0f} mi exceeds the {range_miles:.0f} mi range"
        )

    D = [c.along_miles for c in corridor]
    price = [c.station.price_per_gallon for c in corridor]
    detour = [c.offset_miles for c in corridor]

    # Nodes: 0 = virtual start (free full tank), 1..n = candidates, n+1 = finish.
    size = n + 2
    START, FINISH = 0, n + 1
    dist = [0.0] + D + [route_miles]
    cost = [0.0] + price + [0.0]
    INF = math.inf

    best = [INF] * size
    prev = [-1] * size
    best[START] = 0.0

    for i in range(1, size):
        di = dist[i]
        best_local = INF
        prev_local = -1
        for j in range(i):  # all earlier nodes; reachability = distance gap
            if math.isinf(best[j]):
                continue
            gap = di - dist[j]
            if gap > range_miles + 1e-9:
                continue
            if 0 < i <= n:  # arriving at candidate i adds its detour cost
                unit_price = cost[i] if (j == START and not start_full) else cost[j]
                edge = gap / mpg * unit_price + priority_weight * detour[i - 1]
            else:  # arriving at FINISH
                if j == START and not start_full:
                    continue  # an empty initial tank cannot cover the route unpaid
                edge = gap / mpg * cost[j]
            value = best[j] + edge
            if value < best_local:
                best_local = value
                prev_local = j
        best[i] = best_local
        prev[i] = prev_local

    if math.isinf(best[FINISH]) or prev[FINISH] == -1:
        gap_candidates = _first_blocking_gap(dist, size, START, range_miles)
        raise UnreachableError(
            f"route cannot be fueled: largest reachable gap exceeds the "
            f"{range_miles:.0f} mi range around mile {gap_candidates:.0f}"
        )

    # Reconstruct chosen candidates (exclude virtual START/FINISH).
    chosen: list[int] = []
    node = prev[FINISH]
    while node not in (-1, START):
        chosen.append(node - 1)
        node = prev[node]
    chosen.reverse()

    stops: list[FuelStop] = []
    total_cost = 0.0
    total_gallons = 0.0
    total_detour = 0.0
    # Fuel bought at a stop covers everything burned after it until the next
    # purchase point (or the finish) - matching the DP, which prices each
    # segment at its source station. With start_full the initial tank covers
    # miles 0..D_1 for free, so the first stop only pays from D_1 onward.
    m = len(chosen)
    for k, cand_idx in enumerate(chosen):  # chosen holds candidate indices
        boundary = 0.0 if (k == 0 and not start_full) else D[cand_idx]
        next_dist = route_miles if k + 1 >= m else D[chosen[k + 1]]
        gallons = (next_dist - boundary) / mpg
        stop_cost = gallons * price[cand_idx]
        total_cost += stop_cost
        total_gallons += gallons
        total_detour += detour[cand_idx]
        stops.append(FuelStop(
            station=corridor[cand_idx],
            gallons=gallons,
            cost_usd=stop_cost,
        ))

    return FuelPlan(
        stops=stops,
        total_cost=total_cost,
        total_gallons=total_gallons,
        total_stop_distance_miles=total_detour,
    )


def _first_blocking_gap(dist: list[float], size: int, start: int, range_miles: float) -> float:
    """Mile position just before the first >range gap (for the error message)."""
    reachable = 0.0
    frontier = [start]
    seen = {start}
    while frontier:
        j = frontier.pop()
        for i in range(j + 1, size):
            if i in seen:
                continue
            if dist[i] - dist[j] <= range_miles + 1e-9:
                seen.add(i)
                frontier.append(i)
                reachable = max(reachable, dist[i])
    return reachable
