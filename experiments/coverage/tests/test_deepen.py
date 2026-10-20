"""Experiment 3: second-level splitting and diminishing returns."""

from __future__ import annotations

from geocoverage.deepen import grandchildren, load_cell_union, run_experiment
from geocoverage.geometry import circle_region, local_projection
from geocoverage.gosom import MapView
from geocoverage.repeatability import build_cells
from geocoverage.search import Place

LAT = 19.0635
LON = 72.9986


def test_grandchildren_are_sixteen_named_rectangles() -> None:
    region = circle_region(LAT, LON, 0.75)
    _projection, cells = build_cells(region)
    grands = grandchildren(cells[3])
    assert len(grands) == 16
    assert grands[0][0] == "SW/SW"
    assert grands[-1][0] == "NE/NE"
    minx, miny, maxx, maxy = grands[0][1].bounds
    assert abs((maxx - minx) - 187.5) < 5.0
    assert abs((maxy - miny) - 187.5) < 5.0


def test_load_cell_union_combines_baseline_and_action_news() -> None:
    stored = {
        "cells": [
            {
                "cell": "NE",
                "baseline_ids": ["a", "b"],
                "actions": [
                    {"action": "zoom_in", "new_ids": ["c"]},
                    {"action": "shift", "new_ids": ["c", "d"]},
                    {"action": "split", "new_ids": []},
                ],
            },
            {"cell": "SW", "baseline_ids": [], "actions": []},
        ]
    }
    assert load_cell_union(stored, "NE") == ("a", "b", "c", "d")


def test_level2_counts_only_ids_new_to_the_cell_union() -> None:
    region = circle_region(LAT, LON, 0.75)
    projection, cells = build_cells(region)
    spec = cells[3]
    centre = spec.rect_m.centroid
    clon, clat = projection.inverse.transform(centre.x, centre.y)
    known = Place(place_id="known", lon=float(clon), lat=float(clat), rank=0)
    fresh = Place(place_id="fresh", lon=float(clon), lat=float(clat), rank=1)
    far = Place(place_id="far", lon=0.0, lat=0.0, rank=2)

    def fetch(geometry_m: object, variant: str) -> tuple[MapView, float, list[Place]]:
        del geometry_m, variant
        return MapView(lat=LAT, lon=LON, zoom=19), 10.0, [known, fresh, far]

    payload = run_experiment(
        region,
        area_label="test",
        keyword="restaurant",
        cell_name="NE",
        baseline_ids=("known",),
        fetch=fetch,
    )
    assert payload["baseline_in_target"] == 1
    assert payload["grandchildren_searched"] == 16
    # The same fresh ID returned by every grandchild counts once.
    assert payload["new_in_target_level2"] == 1
    assert payload["union_after_level2"] == 2
    assert local_projection(region) is not None
