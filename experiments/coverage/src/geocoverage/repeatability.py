"""Experiment 1: is one map view repeatable?

The same cell is searched several times with the same keyword, centre, and
zoom. Each repeat is a fresh search, never a cached page. The report compares
the place-ID sets: how many IDs every repeat agrees on, how many only some
repeats return, and how many new IDs each extra repeat adds.

This measures search consistency only. The union of repeats is not every
place in the cell, and no completeness claim is made.

Default area is Vashi (Navi Mumbai was judged too large for a first pilot):
a 0.75 km circle around Vashi station, split geometrically into the four
quadrants of its bounding square. The quadrants are only searched, never
their parent.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from geocoverage.geometry import (
    LocalProjection,
    circle_region,
    covers_lonlat,
    local_projection,
    polygonal,
    root_rect,
    split_rect,
)
from geocoverage.gosom import (
    GosomSearch,
    MapView,
    WorkspaceRunner,
    validate_keyword,
    view_for_geometry,
)
from geocoverage.search import Place, ProviderError

VASHI_LAT = 19.0635
VASHI_LON = 72.9986
DEFAULT_RADIUS_KM = 0.75
DEFAULT_REPEATS = 4
MAX_REPEATS = 6
FAR_OUTSIDE_M = 5_000.0
CELL_NAMES = ("SW", "SE", "NW", "NE")
DEFAULT_OUT = Path(__file__).resolve().parents[2] / ".runs" / "vashi-repeat.json"

FetchCell = Callable[["CellSpec", int], tuple[MapView, float, list[Place]]]


@dataclass(frozen=True)
class CellSpec:
    """One quadrant of the target's bounding square, in projected meters."""

    name: str
    rect_m: Polygon
    target_m: BaseGeometry
    west: float
    south: float
    east: float
    north: float


@dataclass(frozen=True)
class RepeatRun:
    cell: str
    repeat: int
    lat: float
    lon: float
    zoom: int
    seconds: float
    places: tuple[Place, ...] = ()
    error: str = ""


@dataclass
class _CellRuns:
    spec: CellSpec
    runs: list[RepeatRun] = field(default_factory=list)


def build_cells(region: BaseGeometry) -> tuple[LocalProjection, list[CellSpec]]:
    """Quadrants of the region's bounding square. Parents are never searched."""
    projection = local_projection(region)
    region_m = polygonal(projection.to_meters(region))
    root = root_rect(region_m)
    cells: list[CellSpec] = []
    for name, rect in zip(CELL_NAMES, split_rect(root), strict=True):
        bounds_wgs = projection.to_wgs84(rect).bounds
        cells.append(
            CellSpec(
                name=name,
                rect_m=rect,
                target_m=rect.intersection(region_m),
                west=float(bounds_wgs[0]),
                south=float(bounds_wgs[1]),
                east=float(bounds_wgs[2]),
                north=float(bounds_wgs[3]),
            )
        )
    return projection, cells


def _int_value(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("expected an integer report value")
    return value


def _float_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("expected a numeric report value")
    return float(value)


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)


def analyse_cell(cell_runs: _CellRuns, projection: LocalProjection) -> dict[str, object]:
    """Summarise one cell's repeats using IDs inside cell-and-target only."""
    ok_runs = [run for run in cell_runs.runs if not run.error]
    in_target: list[list[Place]] = []
    for run in ok_runs:
        in_target.append(
            [
                place
                for place in run.places
                if covers_lonlat(cell_runs.spec.target_m, place.lon, place.lat, projection)
            ]
        )
    sets = [{place.place_id for place in places} for places in in_target]
    union: set[str] = set().union(*sets) if sets else set()
    core: set[str] = set.intersection(*sets) if sets else set()

    per_repeat: list[dict[str, object]] = []
    seen: set[str] = set()
    for run, places, ids in zip(ok_runs, in_target, sets, strict=True):
        new_ids = ids - seen
        seen |= ids
        far_outside = sum(
            1
            for place in run.places
            if cell_runs.spec.target_m.distance(
                projection.point_meters(place.lon, place.lat)
            )
            > FAR_OUTSIDE_M
        )
        per_repeat.append(
            {
                "repeat": run.repeat,
                "seconds": round(run.seconds, 2),
                "raw_rows": len(run.places),
                "in_target": len(places),
                "far_outside_rows": far_outside,
                "new_in_target": len(new_ids),
                "cumulative_in_target": len(seen),
            }
        )

    # Two empty sets have no overlap evidence; they are not a perfect match.
    pairwise: list[float] = []
    if union:
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                pairwise.append(_jaccard(sets[i], sets[j]))

    rank_diffs: list[float] = []
    if len(ok_runs) > 1 and core:
        rank_maps = [
            {place.place_id: place.rank for place in places} for places in in_target
        ]
        for place_id in core:
            ranks = [ranks_for_run[place_id] for ranks_for_run in rank_maps]
            for i in range(len(ranks)):
                for j in range(i + 1, len(ranks)):
                    rank_diffs.append(abs(ranks[i] - ranks[j]))

    best_single = max((len(ids) for ids in sets), default=0)
    spec = cell_runs.spec
    return {
        "cell": spec.name,
        "bounds_lonlat": [spec.west, spec.south, spec.east, spec.north],
        "view": (
            {"lat": ok_runs[0].lat, "lon": ok_runs[0].lon, "zoom": ok_runs[0].zoom}
            if ok_runs
            else None
        ),
        "repeats_ok": len(ok_runs),
        "repeats_failed": len(cell_runs.runs) - len(ok_runs),
        "errors": [run.error for run in cell_runs.runs if run.error],
        "per_repeat": per_repeat,
        "union_in_target": len(union),
        "core_in_target": len(core),
        "core_fraction_of_union": round(len(core) / len(union), 4) if union else None,
        "best_single_in_target": best_single,
        "extra_ids_from_repeats": len(union) - best_single,
        "mean_pairwise_jaccard": round(sum(pairwise) / len(pairwise), 4) if pairwise else None,
        "min_pairwise_jaccard": round(min(pairwise), 4) if pairwise else None,
        "mean_abs_rank_shift_core": (
            round(sum(rank_diffs) / len(rank_diffs), 2) if rank_diffs else 0.0
        ),
        "union_ids": sorted(union),
        "core_ids": sorted(core),
        "runs": [
            {
                "repeat": run.repeat,
                "lat": run.lat,
                "lon": run.lon,
                "zoom": run.zoom,
                "seconds": round(run.seconds, 2),
                "error": run.error,
                "places": [
                    {
                        "place_id": place.place_id,
                        "lon": place.lon,
                        "lat": place.lat,
                        "rank": place.rank,
                    }
                    for place in run.places
                ],
            }
            for run in cell_runs.runs
        ],
    }


def collect_runs(
    cells: list[CellSpec],
    *,
    projection: LocalProjection,
    repeats: int,
    fetch: FetchCell,
) -> list[_CellRuns]:
    """Run every repeat fresh. A failed repeat is recorded, never invented."""
    collected: list[_CellRuns] = []
    for spec in cells:
        cell_runs = _CellRuns(spec=spec)
        for repeat in range(1, repeats + 1):
            view = view_for_geometry(spec.rect_m, projection)
            try:
                seen_view, seconds, places = fetch(spec, repeat)
            except ProviderError as exc:
                cell_runs.runs.append(
                    RepeatRun(
                        cell=spec.name,
                        repeat=repeat,
                        lat=view.lat,
                        lon=view.lon,
                        zoom=view.zoom,
                        seconds=0.0,
                        error=str(exc),
                    )
                )
                continue
            cell_runs.runs.append(
                RepeatRun(
                    cell=spec.name,
                    repeat=repeat,
                    lat=seen_view.lat,
                    lon=seen_view.lon,
                    zoom=seen_view.zoom,
                    seconds=seconds,
                    places=tuple(places),
                )
            )
        collected.append(cell_runs)
    return collected




def run_experiment(
    region: BaseGeometry,
    *,
    area_label: str,
    keyword: str,
    repeats: int,
    fetch: FetchCell,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    if repeats < 2 or repeats > MAX_REPEATS:
        raise ValueError(f"repeats must be from 2 to {MAX_REPEATS}")
    note = progress or (lambda _text: None)
    projection, cells = build_cells(region)

    def tracked_fetch(spec: CellSpec, repeat: int) -> tuple[MapView, float, list[Place]]:
        note(f"Cell {spec.name} repeat {repeat}/{repeats}: opening map page.")
        view, seconds, places = fetch(spec, repeat)
        note(
            f"Cell {spec.name} repeat {repeat}/{repeats}: "
            f"{len(places)} rows in {seconds:.0f}s."
        )
        return view, seconds, places

    collected = collect_runs(cells, projection=projection, repeats=repeats, fetch=tracked_fetch)
    cell_reports = [analyse_cell(cell_runs, projection) for cell_runs in collected]
    return {
        "experiment": "exp1-repeatability",
        "area": area_label,
        "keyword": keyword,
        "repeats_planned": repeats,
        "cells": cell_reports,
        "summary": _summarise(cell_reports),
        "claim": CLAIM,
    }


CLAIM = (
    "Repeatability evidence only. The union of repeats is a larger "
    "observed set, not every place in the area. Total places unknown."
)


def _summarise(cell_reports: list[dict[str, object]]) -> dict[str, object]:
    jaccards = [
        _float_value(report["mean_pairwise_jaccard"])
        for report in cell_reports
        if report["mean_pairwise_jaccard"] is not None
    ]
    return {
        "mean_pairwise_jaccard": (
            round(sum(jaccards) / len(jaccards), 4) if jaccards else None
        ),
        "cells_with_overlap_evidence": len(jaccards),
        "union_in_target_all_cells": sum(
            _int_value(report["union_in_target"]) for report in cell_reports
        ),
        "best_single_sum_all_cells": sum(
            _int_value(report["best_single_in_target"]) for report in cell_reports
        ),
        "extra_ids_from_repeats_all_cells": sum(
            _int_value(report["extra_ids_from_repeats"]) for report in cell_reports
        ),
    }


def reanalyse_payload(stored: dict[str, object], region: BaseGeometry) -> dict[str, object]:
    """Rebuild the analysis from stored runs. No search is called."""
    projection, cells = build_cells(region)
    specs = {spec.name: spec for spec in cells}
    stored_cells = stored.get("cells")
    if not isinstance(stored_cells, list):
        raise TypeError("stored payload has no cells list")
    collected: list[_CellRuns] = []
    for report in stored_cells:
        if not isinstance(report, dict):
            raise TypeError("stored cell report is malformed")
        name = report.get("cell")
        if not isinstance(name, str) or name not in specs:
            raise ValueError("stored cell report has an unknown cell")
        cell_runs = _CellRuns(spec=specs[name])
        runs = report.get("runs")
        if not isinstance(runs, list):
            raise TypeError("stored cell report has no runs list")
        for run in runs:
            if not isinstance(run, dict):
                raise TypeError("stored run is malformed")
            places: list[Place] = []
            raw_places = run.get("places")
            if not isinstance(raw_places, list):
                raise TypeError("stored run has no places list")
            for raw in raw_places:
                if not isinstance(raw, dict):
                    raise TypeError("stored place is malformed")
                places.append(
                    Place(
                        place_id=str(raw["place_id"]),
                        lon=_float_value(raw["lon"]),
                        lat=_float_value(raw["lat"]),
                        rank=_int_value(raw["rank"]),
                    )
                )
            cell_runs.runs.append(
                RepeatRun(
                    cell=name,
                    repeat=_int_value(run["repeat"]),
                    lat=_float_value(run["lat"]),
                    lon=_float_value(run["lon"]),
                    zoom=_int_value(run["zoom"]),
                    seconds=_float_value(run["seconds"]),
                    places=tuple(places),
                    error=str(run.get("error") or ""),
                )
            )
        collected.append(cell_runs)
    cell_reports = [analyse_cell(cell_runs, projection) for cell_runs in collected]
    return {
        "experiment": "exp1-repeatability",
        "area": str(stored.get("area") or ""),
        "keyword": str(stored.get("keyword") or ""),
        "repeats_planned": _int_value(stored.get("repeats_planned") or 0),
        "cells": cell_reports,
        "summary": _summarise(cell_reports),
        "claim": CLAIM,
    }


def render_text(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    assert isinstance(summary, dict)
    lines = [
        f"Experiment 1 repeatability: {payload['area']}",
        f"Keyword: {payload['keyword']!r}. Repeats planned per cell: {payload['repeats_planned']}.",
        "Each repeat is a fresh map search of the same cell (same centre and zoom, no cache).",
        "",
    ]
    cells = payload["cells"]
    assert isinstance(cells, list)
    for report in cells:
        assert isinstance(report, dict)
        lines.append(
            f"Cell {report['cell']}: union {report['union_in_target']} in-target IDs, "
            f"core (every repeat) {report['core_in_target']}, "
            f"best single repeat {report['best_single_in_target']}, "
            f"extra from repeats {report['extra_ids_from_repeats']}, "
            f"mean overlap (Jaccard) {report['mean_pairwise_jaccard']}, "
            f"min {report['min_pairwise_jaccard']}, "
            f"avg rank shift of core IDs {report['mean_abs_rank_shift_core']}."
        )
        per_repeat = report["per_repeat"]
        assert isinstance(per_repeat, list)
        for entry in per_repeat:
            assert isinstance(entry, dict)
            lines.append(
                f"  repeat {entry['repeat']}: raw {entry['raw_rows']}, "
                f"in-target {entry['in_target']}, new {entry['new_in_target']}, "
                f"cumulative {entry['cumulative_in_target']}, "
                f"far-outside {entry['far_outside_rows']}, {entry['seconds']}s"
            )
        if report["errors"]:
            lines.append(f"  failed repeats: {report['errors']}")
    across = (
        f"Across cells: union {summary['union_in_target_all_cells']}, "
        f"sum of best single repeats {summary['best_single_sum_all_cells']}, "
        f"extra IDs from repeating {summary['extra_ids_from_repeats_all_cells']}, "
        f"mean overlap {summary['mean_pairwise_jaccard']}."
    )
    lines.extend(["", across, str(payload["claim"])])
    return "\n".join(lines) + "\n"


def make_live_fetch(
    *,
    keyword: str,
    projection: LocalProjection,
    runner: WorkspaceRunner,
) -> FetchCell:
    def fetch(spec: CellSpec, repeat: int) -> tuple[MapView, float, list[Place]]:
        del repeat
        # A new adapter per repeat: GosomSearch caches an identical view
        # inside one adapter, and a cached page is not a repeat.
        search = GosomSearch(keyword=keyword, projection=projection, fetcher=runner)
        places = search.search(spec.rect_m)
        record = search.fetches[-1]
        return MapView(lat=record.lat, lon=record.lon, zoom=record.zoom), record.seconds, places

    return fetch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Experiment 1: search the same Vashi cells several times and "
            "compare the place-ID sets. Calls Google Maps unless --dry-plan."
        )
    )
    parser.add_argument("--lat", type=float, default=VASHI_LAT, help="Centre latitude (default: Vashi station)")
    parser.add_argument("--lon", type=float, default=VASHI_LON, help="Centre longitude (default: Vashi station)")
    parser.add_argument("--radius-km", type=float, default=DEFAULT_RADIUS_KM)
    parser.add_argument("--area-label", default="Vashi")
    parser.add_argument("--keyword", default="restaurant")
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=1200.0)
    parser.add_argument("--image", default="gosom/google-maps-scraper:latest")
    parser.add_argument("--binary", default="")
    parser.add_argument("--proxies-file", type=Path)
    parser.add_argument("--lang", default="en")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--reanalyse",
        type=Path,
        help="Rebuild the report from a stored JSON payload. No Google calls.",
    )
    parser.add_argument(
        "--dry-plan",
        action="store_true",
        help="Print cells and views only. No Google calls.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.repeats < 2 or args.repeats > MAX_REPEATS:
        parser.error(f"repeats must be from 2 to {MAX_REPEATS}")
    try:
        keyword = validate_keyword(args.keyword)
        region = circle_region(args.lat, args.lon, args.radius_km)
    except ValueError as exc:
        parser.error(str(exc))
    area_label = (
        f"{args.area_label} (circle of {args.radius_km} km around "
        f"latitude {args.lat}, longitude {args.lon})"
    )
    projection, cells = build_cells(region)
    if args.reanalyse is not None:
        stored = json.loads(args.reanalyse.read_text(encoding="utf-8"))
        if not isinstance(stored, dict):
            parser.error("stored payload is not a JSON object")
        payload = reanalyse_payload(stored, region)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        sys.stdout.write(render_text(payload))
        print(f"Wrote {args.out.resolve()}", file=sys.stderr)
        return 0
    if args.dry_plan:
        print(f"Dry plan for {area_label}. Keyword {keyword!r}. No Google calls.")
        for spec in cells:
            view = view_for_geometry(spec.rect_m, projection)
            print(
                f"Cell {spec.name}: bounds lonlat "
                f"[{spec.west:.6f}, {spec.south:.6f}, {spec.east:.6f}, {spec.north:.6f}] "
                f"view lat={view.lat:.6f} lon={view.lon:.6f} zoom={view.zoom} "
                f"x {args.repeats} repeats"
            )
        print(f"Total fresh searches planned: {len(cells) * args.repeats}")
        return 0
    if args.proxies_file is not None and not args.proxies_file.is_file():
        parser.error("proxies file is missing")
    runner = WorkspaceRunner(
        mode="binary" if args.binary else "docker",
        binary=tuple(args.binary.split()) if args.binary else (),
        image=args.image,
        depth=args.depth,
        lang=args.lang,
        timeout_s=args.timeout_seconds,
        proxies_file=args.proxies_file,
        fetch_budget=len(cells) * args.repeats,
        on_event=lambda text: print(text, file=sys.stderr, flush=True),
    )
    fetch = make_live_fetch(keyword=keyword, projection=projection, runner=runner)
    print(
        f"Calling Google Maps {len(cells) * args.repeats} times for {area_label}. "
        "Each full page can take a few minutes.",
        file=sys.stderr,
    )
    payload = run_experiment(
        region,
        area_label=area_label,
        keyword=keyword,
        repeats=args.repeats,
        fetch=fetch,
        progress=lambda text: print(text, file=sys.stderr, flush=True),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(render_text(payload))
    print(f"Wrote {args.out.resolve()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
