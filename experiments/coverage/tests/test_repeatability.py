"""Experiment 1 analysis: repeated searches of the same cell."""

from __future__ import annotations

from shapely.geometry import box

from geocoverage.geometry import circle_region, local_projection
from geocoverage.gosom import MapView
from geocoverage.repeatability import (
    build_cells,
    collect_runs,
    run_experiment,
)
from geocoverage.search import Place, ProviderError


def _place(place_id: str, rank: int, lon: float = 72.9986, lat: float = 19.0635) -> Place:
    return Place(place_id=place_id, lon=lon, lat=lat, rank=rank)


def test_build_cells_makes_four_quadrants_without_a_parent() -> None:
    region = circle_region(19.0635, 72.9986, 0.75)
    _projection, cells = build_cells(region)
    assert [cell.name for cell in cells] == ["SW", "SE", "NW", "NE"]
    for cell in cells:
        minx, miny, maxx, maxy = cell.rect_m.bounds
        assert abs((maxx - minx) - 750.0) < 5.0
        assert abs((maxy - miny) - 750.0) < 5.0


def test_repeats_report_union_core_and_marginal_gain() -> None:
    region = circle_region(19.0635, 72.9986, 0.75)
    pages = {
        1: [_place("a", 0), _place("b", 1), _place("c", 2)],
        2: [_place("a", 1), _place("b", 0), _place("d", 2)],
        3: [_place("a", 0), _place("b", 1), _place("c", 2), _place("d", 3)],
    }

    def fetch(spec: object, repeat: int) -> tuple[MapView, float, list[Place]]:
        del spec
        return MapView(lat=19.0635, lon=72.9986, zoom=17), 10.0, pages[repeat]

    payload = run_experiment(
        region,
        area_label="test",
        keyword="restaurant",
        repeats=3,
        fetch=fetch,
    )
    cells = payload["cells"]
    assert isinstance(cells, list)
    for report in cells:
        assert isinstance(report, dict)
        # Places sit at the region centre, so only one quadrant holds them;
        # the other quadrants honestly report empty repeats.
        if report["union_in_target"]:
            assert report["union_in_target"] == 4
            assert report["core_in_target"] == 2
            assert report["best_single_in_target"] == 4
            assert report["extra_ids_from_repeats"] == 0
            per_repeat = report["per_repeat"]
            assert isinstance(per_repeat, list)
            assert [entry["new_in_target"] for entry in per_repeat] == [3, 1, 0]  # type: ignore[index]
            assert report["mean_pairwise_jaccard"] < 1.0


def test_failed_repeat_is_recorded_not_invented() -> None:
    region = circle_region(19.0635, 72.9986, 0.75)
    projection, cells = build_cells(region)
    calls = {"count": 0}

    def fetch(spec: object, repeat: int) -> tuple[MapView, float, list[Place]]:
        del spec
        calls["count"] += 1
        if repeat == 2:
            raise ProviderError("map page failed")
        return MapView(lat=19.0635, lon=72.9986, zoom=17), 5.0, [_place("a", 0)]

    collected = collect_runs(cells[:1], projection=projection, repeats=3, fetch=fetch)
    runs = collected[0].runs
    assert calls["count"] == 3
    assert [run.error for run in runs] == ["", "map page failed", ""]
    assert local_projection(region) is not None
    assert box(0, 0, 1, 1).area == 1.0
