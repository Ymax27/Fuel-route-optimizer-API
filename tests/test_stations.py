"""Unit tests for routing.stations (parsing + corridor selection)."""

from __future__ import annotations

import numpy as np
import pytest

from routing.stations import Station, StationDataError, StationStore, load_stations


def line_store(lats_prices: list[tuple[float, float]]) -> StationStore:
    stations = [
        Station(opis_id=str(i), name=f"S{i}", city="X", state="KS",
                lat=lat, lon=-98.0, price_per_gallon=price)
        for i, (lat, price) in enumerate(lats_prices)
    ]
    return StationStore(stations)


class TestLoadStations:
    def test_missing_file_raises_clear_error(self, tmp_path):
        from routing.stations import StationDataError, load_stations
        with pytest.raises(StationDataError, match="run 'python scripts"):
            load_stations(tmp_path / "nope.csv")

    def test_bad_columns_raise(self, tmp_path):
        f = tmp_path / "bad.csv"
        f.write_text("foo,bar\n1,2\n")
        with pytest.raises(StationDataError, match="missing columns"):
            load_stations(f)

    def test_malformed_row_raises_with_id(self, tmp_path):
        f = tmp_path / "bad.csv"
        f.write_text("opis_id,name,city,state,lat,lon,price_per_gallon\n"
                     "7,Name,City,KS,not_a_float,-98.0,3.0\n")
        with pytest.raises(StationDataError, match="OPIS id '7'"):
            load_stations(f)


class TestCorridor:
    def test_ordering_and_along_values(self):
        store = line_store([(39.0, 3.0), (39.5, 2.0), (40.0, 4.0)])
        # Route along the same line, 0 to 100 miles.
        coords = np.array([[-98.0, 38.0], [-98.0, 39.2], [-98.0, 40.4]])
        cum = np.array([0.0, 83.0, 166.0])
        cands = store.corridor_candidates(coords, cum, radius_miles=25,
                                          max_per_window=40, window_miles=0.5)
        assert [c.station.opis_id for c in cands] == ["0", "1", "2"]
        assert all(c.along_miles > 0 for c in cands)
        assert cands[1].station.price_per_gallon == 2.0

    def test_window_pruning_keeps_cheapest(self):
        # Two stations essentially at the same location; the cheap one survives.
        store = line_store([(39.0, 4.0), (39.0001, 2.0)])
        coords = np.array([[-98.0, 38.5], [-98.0, 39.5]])
        cum = np.array([0.0, 69.0])
        cands = store.corridor_candidates(coords, cum, radius_miles=30,
                                          max_per_window=1, window_miles=0.5)
        assert len(cands) == 1
        assert cands[0].station.price_per_gallon == 2.0

    def test_far_station_excluded(self):
        store = line_store([(39.0, 1.0)])
        coords = np.array([[-98.0, 38.0], [-98.0, 38.2]])
        cum = np.array([0.0, 14.0])
        cands = store.corridor_candidates(coords, cum, radius_miles=5,
                                          max_per_window=10, window_miles=0.5)
        assert cands == []

    def test_along_position_between_sparse_vertices(self):
        # Station between two far-apart route vertices must still be found
        # (sample points are interpolated along segments, not snapped).
        store = line_store([(39.6, 2.0)])
        coords = np.array([[-98.0, 38.0], [-98.0, 40.4]])  # one 166-mile segment
        cum = np.array([0.0, 166.0])
        cands = store.corridor_candidates(coords, cum, radius_miles=15,
                                          max_per_window=10, window_miles=0.5)
        assert len(cands) == 1
        assert cands[0].along_miles == pytest.approx((39.6 - 38.0) * 69.0, abs=3.0)
        assert cands[0].offset_miles == pytest.approx(0.0, abs=0.1)
