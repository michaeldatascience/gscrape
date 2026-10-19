"""Run the offline single-search versus planner comparison and print the counts."""

from __future__ import annotations

import json

from geocoverage.compare import compare_searches
from geocoverage.fixtures import urban_500
from geocoverage.geometry import local_projection
from geocoverage.search import MockSearch


def main() -> None:
    region, places = urban_500()
    projection = local_projection(region)
    search = MockSearch(places, cap=120, projection=projection)
    comparison = compare_searches(
        region,
        search,
        max_searches=1000,
        probe_fraction=1.0,
        seed=0,
    )
    payload = {
        "adapter": "mock",
        "cap": search.cap,
        "known_places": len(places),
        "single_search_in_polygon": comparison.single_search_in_polygon,
        "planner_in_polygon": comparison.planner_in_polygon,
        "overlap": len(comparison.overlap),
        "only_single_search": len(comparison.only_single_search),
        "only_planner": len(comparison.only_planner),
        "out_of_polygon_single_search": comparison.out_of_polygon_single_search,
        "out_of_polygon_planner": comparison.out_of_polygon_planner,
        "planner_status": comparison.planner_report.status,
        "cells_searched": comparison.planner_report.cells_searched,
        "cells_split": comparison.planner_report.cells_split,
        "claim": comparison.claim,
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
