"""One viewport versus the recursive planner, on the same search adapter."""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry

from geocoverage.geometry import covers_lonlat, polygonal, root_rect
from geocoverage.planner import CoverageReport, discover
from geocoverage.search import SearchAdapter


@dataclass(frozen=True)
class SearchComparison:
    overlap: tuple[str, ...]
    only_single_search: tuple[str, ...]
    only_planner: tuple[str, ...]
    out_of_polygon_single_search: int
    out_of_polygon_planner: int
    single_search_in_polygon: int
    planner_in_polygon: int
    planner_report: CoverageReport

    @property
    def claim(self) -> str:
        return (
            f"single search found {self.single_search_in_polygon} in-polygon IDs; "
            f"planner found {self.planner_in_polygon}; "
            f"{len(self.only_planner)} IDs appeared only after splitting; "
            "total available places unknown"
        )


def compare_searches(
    region: BaseGeometry,
    search: SearchAdapter,
    *,
    max_searches: int = 1000,
    min_cell_m: float = 200.0,
    max_depth: int = 12,
    probe_fraction: float = 0.1,
    seed: int = 0,
) -> SearchComparison:
    """Arm A is one search of the region's bounding view. Arm B is ``discover``."""
    projection = search.projection
    region_m = polygonal(projection.to_meters(region))
    view = root_rect(region_m)
    arm_a = search.search(view, variant="primary")

    inside: set[str] = set()
    outside = 0
    for place in arm_a:
        if covers_lonlat(region_m, place.lon, place.lat, projection):
            inside.add(place.place_id)
        else:
            outside += 1

    # The single search above already consumed a provider view. The planner
    # runs its own views against the same adapter; callers that share a live
    # budget must account for both arms themselves.
    report = discover(
        region,
        search,
        max_searches=max_searches,
        min_cell_m=min_cell_m,
        max_depth=max_depth,
        probe_fraction=probe_fraction,
        seed=seed,
    )
    planner_ids = set(report.in_polygon_ids)
    return SearchComparison(
        overlap=tuple(sorted(inside & planner_ids)),
        only_single_search=tuple(sorted(inside - planner_ids)),
        only_planner=tuple(sorted(planner_ids - inside)),
        out_of_polygon_single_search=outside,
        out_of_polygon_planner=report.out_of_polygon_rows,
        single_search_in_polygon=len(inside),
        planner_in_polygon=len(planner_ids),
        planner_report=report,
    )

