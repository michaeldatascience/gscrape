"""Adaptive quadtree. Split saturated views; probes keep quiet pages honest."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from geocoverage.geometry import (
    covers_lonlat,
    min_side_m,
    polygonal,
    require_region,
    root_rect,
    split_rect,
)
from geocoverage.search import Place, ProviderError, SearchAdapter

SATURATION_FRACTION = 0.8


@dataclass(frozen=True)
class UnresolvedCell:
    path: str
    reason: str


@dataclass(frozen=True)
class CoverageReport:
    status: str
    raw_observations: int
    unique_place_ids: tuple[str, ...]
    in_polygon_ids: tuple[str, ...]
    out_of_polygon_rows: int
    cells_searched: int
    cells_split: int
    unresolved: tuple[UnresolvedCell, ...]
    claim: str


@dataclass
class _Tile:
    path: str
    rect_m: Polygon
    depth: int


def _probe_selected(path: str, seed: int, fraction: float) -> bool:
    if fraction <= 0:
        return False
    if fraction >= 1:
        return True
    digest = hashlib.sha256(f"{seed}:{path}".encode()).digest()
    sample = int.from_bytes(digest[:8], "big") / 2**64
    return sample < fraction


def _saturated(returned_rows: int, cap: int) -> bool:
    return returned_rows >= SATURATION_FRACTION * cap


def _can_split(tile: _Tile, *, min_cell_m: float, max_depth: int) -> bool:
    if tile.depth >= max_depth:
        return False
    return min_side_m(tile.rect_m) / 2.0 >= min_cell_m


def discover(
    region: BaseGeometry,
    search: SearchAdapter,
    *,
    max_searches: int = 1000,
    min_cell_m: float = 200.0,
    max_depth: int = 12,
    probe_fraction: float = 0.1,
    seed: int = 0,
) -> CoverageReport:
    """Search ``region`` through ``search`` and report IDs plus unresolved cells.

    ``status`` is operational evidence. It is never a percentage of all businesses.
    """
    require_region(region)
    if max_searches < 1:
        raise ValueError("max_searches must be at least 1")

    projection = search.projection
    region_m = polygonal(projection.to_meters(region))
    if region_m.is_empty:
        raise ValueError("region has no area in the local projection")

    queue: list[_Tile] = [_Tile(path="0", rect_m=root_rect(region_m), depth=0)]
    found: dict[str, Place] = {}
    raw_observations = 0
    out_of_polygon_rows = 0
    cells_searched = 0
    cells_split = 0
    provider_calls = 0
    unresolved: list[UnresolvedCell] = []

    def clip(observations: list[Place]) -> tuple[list[Place], int]:
        kept: list[Place] = []
        outside = 0
        for place in observations:
            if covers_lonlat(region_m, place.lon, place.lat, projection):
                kept.append(place)
            else:
                outside += 1
        return kept, outside

    def remember(places: list[Place]) -> None:
        for place in places:
            found.setdefault(place.place_id, place)

    def spend() -> bool:
        nonlocal provider_calls
        if provider_calls >= max_searches:
            return False
        provider_calls += 1
        return True

    def mark_budget(tiles: list[_Tile]) -> None:
        for tile in tiles:
            unresolved.append(UnresolvedCell(path=tile.path, reason="BUDGET_EXHAUSTED"))

    while queue:
        tile = queue.pop(0)
        if provider_calls >= max_searches:
            mark_budget([tile, *queue])
            queue.clear()
            break

        effective = polygonal(tile.rect_m.intersection(region_m))
        if effective.is_empty or effective.area == 0:
            continue

        spend()
        try:
            primary = search.search(effective, variant="primary")
        except ProviderError:
            unresolved.append(UnresolvedCell(path=tile.path, reason="PROVIDER_ERROR"))
            continue
        cells_searched += 1
        raw_observations += len(primary)
        kept, outside = clip(primary)
        out_of_polygon_rows += outside
        remember(kept)

        splittable = _can_split(tile, min_cell_m=min_cell_m, max_depth=max_depth)
        if _saturated(len(primary), search.cap):
            if splittable:
                _enqueue_children(queue, tile, region_m)
                cells_split += 1
                continue
            unresolved.append(UnresolvedCell(path=tile.path, reason="MIN_GEOMETRY"))
            continue

        probe_new: list[Place] = []
        if _probe_selected(tile.path, seed, probe_fraction):
            if not spend():
                unresolved.append(UnresolvedCell(path=tile.path, reason="BUDGET_EXHAUSTED"))
                mark_budget(queue)
                queue.clear()
                break
            try:
                probed = search.search(effective, variant="probe")
            except ProviderError:
                unresolved.append(UnresolvedCell(path=tile.path, reason="PROVIDER_ERROR"))
                continue
            raw_observations += len(probed)
            probe_kept, probe_outside = clip(probed)
            out_of_polygon_rows += probe_outside
            primary_ids = {place.place_id for place in kept}
            probe_new = [place for place in probe_kept if place.place_id not in primary_ids]
            remember(probe_kept)

        if probe_new and splittable:
            _enqueue_children(queue, tile, region_m)
            cells_split += 1
            continue
        if probe_new and not splittable:
            unresolved.append(UnresolvedCell(path=tile.path, reason="RANKING_RISK"))
            continue

    status = _status(unresolved)
    ordered = tuple(sorted(found))
    return CoverageReport(
        status=status,
        raw_observations=raw_observations,
        unique_place_ids=ordered,
        in_polygon_ids=ordered,
        out_of_polygon_rows=out_of_polygon_rows,
        cells_searched=cells_searched,
        cells_split=cells_split,
        unresolved=tuple(unresolved),
        claim=f"{len(ordered)} place IDs discovered; total available places unknown",
    )


def _enqueue_children(queue: list[_Tile], tile: _Tile, region_m: BaseGeometry) -> None:
    for index, rect in enumerate(split_rect(tile.rect_m)):
        if polygonal(rect.intersection(region_m)).is_empty:
            continue
        queue.append(_Tile(path=f"{tile.path}{index}", rect_m=rect, depth=tile.depth + 1))


def _status(unresolved: list[UnresolvedCell]) -> str:
    if any(cell.reason == "BUDGET_EXHAUSTED" for cell in unresolved):
        return "BUDGET_EXHAUSTED"
    if unresolved:
        return "UNRESOLVED"
    return "HIGH_EVIDENCE"
