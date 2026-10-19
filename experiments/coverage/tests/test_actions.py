"""Experiment 2: split, zoom-in, and shift compared on in-target IDs."""

from __future__ import annotations

from shapely.geometry import Point

from geocoverage.actions import (
    ACTION_NAMES,
    load_baselines,
    planned_searches,
    run_action,
    run_experiment,
    shifted_rect,
)
from geocoverage.geometry import circle_region, local_projection
from geocoverage.gosom import MapView
from geocoverage.repeatability import build_cells
from geocoverage.search import Place, ProviderError

LAT = 19.0635
LON = 72.9986


def _place_at(place_id: str, rank: int, point_m: Point) -> Place:
    region = circle_region(LAT, LON, 0.75)
    projection = local_projection(region)
    lon, lat = projection.inverse.transform(point_m.x, point_m.y)
    return Place(place_id=place_id, lon=float(lon), lat=float(lat), rank=rank)


def test_shift_moves_the_rectangle_east_and_north_by_a_quarter() -> None:
    region = circle_region(LAT, LON, 0.75)
    _projection, cells = build_cells(region)
    rect = cells[0].rect_m
    minx, miny, maxx, maxy = rect.bounds
    shifted = shifted_rect(rect)
    sminx, sminy, smaxx, _smaxy = shifted.bounds
    assert abs((sminx - minx) - (maxx - minx) * 0.25) < 1e-6
    assert abs((sminy - miny) - (maxy - miny) * 0.25) < 1e-6
    assert abs((smaxx - sminx) - (maxx - minx)) < 1e-6


def test_split_searches_four_children_and_counts_only_new_in_target() -> None:
    region = circle_region(LAT, LON, 0.75)
    projection, cells = build_cells(region)
    spec = cells[3]  # NE
    centre = spec.rect_m.centroid
    inside = _place_at("inside-new", 0, centre)
    outside = Place(place_id="outside", lon=0.0, lat=0.0, rank=1)
    calls: list[str] = []

    def fetch(geometry_m: object, variant: str) -> tuple[MapView, float, list[Place]]:
        calls.append(variant)
        return MapView(lat=LAT, lon=LON, zoom=18), 30.0, [inside, outside]

    outcome = run_action(spec, "split", projection=projection, fetch=fetch)
    assert calls == ["primary"] * 4
    assert outcome.searches == 4
    assert outcome.raw_rows == 8
    assert outcome.in_target_ids == ("inside-new",)


def test_failed_subsearch_is_recorded_and_the_rest_still_count() -> None:
    region = circle_region(LAT, LON, 0.75)
    projection, cells = build_cells(region)
    spec = cells[3]
    attempts = {"count": 0}

    def fetch(geometry_m: object, variant: str) -> tuple[MapView, float, list[Place]]:
        del geometry_m, variant
        attempts["count"] += 1
        if attempts["count"] == 2:
            raise ProviderError("child page failed")
        return MapView(lat=LAT, lon=LON, zoom=18), 10.0, []

    outcome = run_action(spec, "split", projection=projection, fetch=fetch)
    assert outcome.errors == ("child page failed",)
    assert outcome.seconds == 30.0


def test_baselines_come_from_the_first_repeat_only() -> None:
    region = circle_region(LAT, LON, 0.75)
    projection, cells = build_cells(region)
    stored = {
        "cells": [
            {
                "cell": spec.name,
                "runs": [
                    {
                        "places": [
                            {"place_id": f"{spec.name}-one", "lon": LON, "lat": LAT, "rank": 0},
                            {"place_id": "far", "lon": 0.0, "lat": 0.0, "rank": 1},
                        ]
                    },
                    {"places": [{"place_id": "later-repeat", "lon": LON, "lat": LAT, "rank": 0}]},
                ],
            }
            for spec in cells
        ]
    }
    baselines = load_baselines(stored, cells, projection)
    # The centre point is a shared corner; every quadrant keeps the in-target
    # first-repeat ID and drops the later repeat and the far-away row.
    for spec in cells:
        assert baselines[spec.name] == (f"{spec.name}-one",)


def test_experiment_reports_new_per_search_for_each_action() -> None:
    region = circle_region(LAT, LON, 0.75)
    _projection, cells = build_cells(region)
    baselines = {spec.name: () for spec in cells}
    ne_centre = cells[3].rect_m.centroid
    fresh = _place_at("fresh", 0, ne_centre)

    def fetch(geometry_m: object, variant: str) -> tuple[MapView, float, list[Place]]:
        del geometry_m
        if variant == "probe":
            return MapView(lat=LAT, lon=LON, zoom=18), 60.0, [fresh]
        return MapView(lat=LAT, lon=LON, zoom=17), 60.0, []

    payload = run_experiment(
        region,
        area_label="test",
        keyword="restaurant",
        baselines=baselines,
        fetch=fetch,
        actions=ACTION_NAMES,
    )
    reports = payload["cells"]
    assert isinstance(reports, list)
    ne = reports[3]
    assert isinstance(ne, dict)
    entries = {entry["action"]: entry for entry in ne["actions"]}  # type: ignore[index, union-attr]
    assert entries["zoom_in"]["new_vs_baseline"] == 1
    assert entries["zoom_in"]["new_per_search"] == 1.0
    assert entries["split"]["searches"] == 4
    assert entries["split"]["new_vs_baseline"] == 0
    assert planned_searches(cells, ACTION_NAMES) == 24
