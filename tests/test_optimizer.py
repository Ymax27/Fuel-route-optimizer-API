"""Unit tests for routing.optimizer.plan_fuel_stops."""

from __future__ import annotations

import pytest

from routing.optimizer import UnreachableError, plan_fuel_stops
from routing.stations import CorridorStation, Station


def make_station(idx: int, price: float) -> Station:
    return Station(
        opis_id=str(idx),
        name=f"Station {idx}",
        city="Somewhere",
        state="KS",
        lat=38.0 + idx * 0.01,
        lon=-98.0,
        price_per_gallon=price,
    )


def corridor_of(*entries: tuple[float, float]) -> list[CorridorStation]:
    """entries: (along_miles, price_per_gallon) with zero detour."""
    return [
        CorridorStation(station=make_station(i, price), along_miles=along, offset_miles=1.0)
        for i, (along, price) in enumerate(entries)
    ]


class TestBasicPlans:
    def test_short_route_needs_no_stop(self):
        plan = plan_fuel_stops(corridor_of((100, 3.0)), route_miles=300, mpg=10,
                               range_miles=500, start_full=True)
        assert plan.stops == []
        assert plan.total_cost == 0.0
        assert plan.total_gallons == 0.0

    def test_single_stop_pays_only_for_miles_after_tank(self):
        # Tank covers 0-500 free; buying at mile 400 must cover 400-700.
        plan = plan_fuel_stops(corridor_of((400, 3.0)), route_miles=700, mpg=10,
                               range_miles=500, start_full=True)
        assert len(plan.stops) == 1
        stop = plan.stops[0]
        assert stop.gallons == pytest.approx(30.0)  # 300 mi / 10 mpg
        assert stop.cost_usd == pytest.approx(90.0)
        assert plan.total_cost == pytest.approx(90.0)
        assert plan.total_gallons == pytest.approx(30.0)

    def test_no_candidates_but_feasible(self):
        plan = plan_fuel_stops([], route_miles=500, mpg=10, range_miles=500)
        assert plan.stops == []
        assert plan.total_cost == 0.0

    def test_zero_route(self):
        plan = plan_fuel_stops(corridor_of((10, 3.0)), route_miles=0, mpg=10, range_miles=500)
        assert plan.stops == []


class TestOptimality:
    def test_skips_expensive_first_station(self):
        # Naive "stop at first reachable station" would buy at 450 (price 5).
        # Optimal is to push to mile 460 and pay 2.0/gal for 440 mi of fuel.
        corridor = corridor_of((450, 5.0), (460, 2.0))
        plan = plan_fuel_stops(corridor, route_miles=900, mpg=10,
                               range_miles=500, start_full=True)
        assert [s.station.station.opis_id for s in plan.stops] == ["1"]
        assert plan.total_cost == pytest.approx(44.0 * 2.0)

    def test_prefers_cheap_station_within_window(self):
        # Both reachable from start at the same moment: must pick the cheap one.
        corridor = corridor_of((300, 4.5), (350, 3.0))
        plan = plan_fuel_stops(corridor, route_miles=800, mpg=10,
                               range_miles=500, start_full=True)
        assert [s.station.station.opis_id for s in plan.stops] == ["1"]
        assert plan.total_cost == pytest.approx((800 - 350) / 10 * 3.0)

    def test_multi_stop_plan_is_exact(self):
        # 1200 mi, stations at 400($5), 500($2), 900($3). Optimal: skip the
        # expensive early stop, buy at 500 for the 500->900 leg, then at 900.
        corridor = corridor_of((400, 5.0), (500, 2.0), (900, 3.0))
        plan = plan_fuel_stops(corridor, route_miles=1200, mpg=10,
                               range_miles=500, start_full=True)
        bought = sum(s.gallons for s in plan.stops)
        assert plan.total_gallons == pytest.approx(bought)
        assert plan.total_gallons == pytest.approx((1200 - 500) / 10)  # free first tank
        assert plan.total_cost == pytest.approx(40 * 2.0 + 30 * 3.0)  # $170
        assert [s.station.station.opis_id for s in plan.stops] == ["1", "2"]

    def test_unreachable_tail_raises_even_with_early_stations(self):
        # Cheap stations early, but nothing between 500 and the 1200-mile finish.
        corridor = corridor_of((400, 5.0), (500, 2.0))
        with pytest.raises(UnreachableError):
            plan_fuel_stops(corridor, route_miles=1200, mpg=10,
                            range_miles=500, start_full=True)

    def test_must_buy_at_expensive_station_when_forced(self):
        # Only station within range at the critical point.
        corridor = corridor_of((450, 6.0))
        plan = plan_fuel_stops(corridor, route_miles=900, mpg=10,
                               range_miles=500, start_full=True)
        assert plan.total_cost == pytest.approx((900 - 450) / 10 * 6.0)


class TestReachability:
    def test_gap_larger_than_range_raises(self):
        corridor = corridor_of((300, 3.0))
        with pytest.raises(UnreachableError):
            plan_fuel_stops(corridor, route_miles=1200, mpg=10,
                            range_miles=500, start_full=True)

    def test_no_candidates_long_route_raises(self):
        with pytest.raises(UnreachableError):
            plan_fuel_stops([], route_miles=600, mpg=10, range_miles=500)


class TestStartNotFull:
    def test_first_stop_pays_from_mile_zero(self):
        corridor = corridor_of((50, 3.0))
        plan = plan_fuel_stops(corridor, route_miles=100, mpg=10,
                               range_miles=500, start_full=False)
        assert len(plan.stops) == 1
        assert plan.stops[0].gallons == pytest.approx(10.0)  # whole route
        assert plan.total_cost == pytest.approx(30.0)


class TestTieBreaking:
    def test_equal_cost_prefers_closer_station(self):
        # Two stations with equal prices; the closer one makes the same plan
        # cheaper only via the tiny detour priority, never via fuel cost.
        c1 = CorridorStation(station=make_station(0, 3.0), along_miles=300, offset_miles=10.0)
        c2 = CorridorStation(station=make_station(1, 3.0), along_miles=305, offset_miles=2.0)
        plan = plan_fuel_stops([c1, c2], route_miles=800, mpg=10,
                               range_miles=500, start_full=True)
        assert plan.total_cost <= plan_fuel_stops(
            [c2, c1], route_miles=800, mpg=10, range_miles=500, start_full=True
        ).total_cost + 1e-9
        assert plan.total_stop_distance_miles == pytest.approx(2.0)
