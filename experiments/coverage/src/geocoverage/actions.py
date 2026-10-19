"""Experiment 2: which action buys new in-target IDs for a cell?

For each Experiment 1 cell, the baseline is that cell's first repeat (one
ordinary search). Three actions are then tried independently against the
same baseline:

- ``zoom_in``: same centre, one zoom level deeper (1 search).
- ``shift``: same zoom, centre shifted 25% of the cell width east and 25%
  north (1 search).
- ``split``: the four child rectangles (4 searches).

Every action's places are clipped to the original cell-and-circle target
before counting. Actions are compared on new in-target IDs per search and
per minute, never on raw rows: Experiment 1 showed raw rows churn outside
the target and can be high where in-target IDs are zero.

No completeness claim is made. An action's union with the baseline is a
larger observed set, not every place in the cell.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from geocoverage.geometry import (
    LocalProjection,
    circle_region,
    covers_lonlat,
    split_rect,
)
from geocoverage.gosom import (
    GosomSearch,
    MapView,
    WorkspaceRunner,
    validate_keyword,
    view_for_geometry,
)
from geocoverage.repeatability import (
    DEFAULT_RADIUS_KM,
    VASHI_LAT,
    VASHI_LON,
    CellSpec,
    _float_value,
    _int_value,
    build_cells,
)
from geocoverage.search import Place, ProviderError, SearchVariant

DEFAULT_BASELINE = Path(__file__).resolve().parents[2] / ".runs" / "vashi-repeat.json"
DEFAULT_OUT = Path(__file__).resolve().parents[2] / ".runs" / "vashi-actions.json"
SHIFT_FRACTION = 0.25
ACTION_NAMES = ("zoom_in", "shift", "split")

FetchView = Callable[[BaseGeometry, SearchVariant], tuple[MapView, float, list[Place]]]

CLAIM = (
    "Action-comparison evidence only. Unions are larger observed sets, "
    "not every place in the cell. Total places unknown."
)


@dataclass(frozen=True)
class ActionOutcome:
    action: str
    searches: int
    seconds: float
    raw_rows: int
    in_target_ids: tuple[str, ...]
    errors: tuple[str, ...] = ()


@dataclass
class _CellActions:
    spec: CellSpec
    baseline_ids: tuple[str, ...]
    outcomes: list[ActionOutcome] = field(default_factory=list)


def shifted_rect(rect_m: BaseGeometry, *, fraction: float = SHIFT_FRACTION) -> BaseGeometry:
    """The same rectangle moved ``fraction`` of its size east and north."""
    minx, miny, maxx, maxy = rect_m.bounds
    dx = (maxx - minx) * fraction
    dy = (maxy - miny) * fraction
    return box(minx + dx, miny + dy, maxx + dx, maxy + dy)


def load_baselines(
    stored: dict[str, object],
    cells: list[CellSpec],
    projection: LocalProjection,
) -> dict[str, tuple[str, ...]]:
    """First-repeat in-target IDs per cell, from an Experiment 1 payload."""
    stored_cells = stored.get("cells")
    if not isinstance(stored_cells, list):
        raise TypeError("baseline payload has no cells list")
    specs = {spec.name: spec for spec in cells}
    baselines: dict[str, tuple[str, ...]] = {}
    for report in stored_cells:
        if not isinstance(report, dict):
            raise TypeError("baseline cell report is malformed")
        name = report.get("cell")
        if not isinstance(name, str) or name not in specs:
            raise ValueError("baseline cell report has an unknown cell")
        runs = report.get("runs")
        if not isinstance(runs, list) or not runs:
            raise TypeError("baseline cell report has no runs")
        first = runs[0]
        if not isinstance(first, dict):
            raise TypeError("baseline run is malformed")
        raw_places = first.get("places")
        if not isinstance(raw_places, list):
            raise TypeError("baseline run has no places list")
        ids: list[str] = []
        for raw in raw_places:
            if not isinstance(raw, dict):
                raise TypeError("baseline place is malformed")
            lon = raw.get("lon")
            lat = raw.get("lat")
            place_id = raw.get("place_id")
            if (
                isinstance(lon, bool)
                or isinstance(lat, bool)
                or not isinstance(lon, (int, float))
                or not isinstance(lat, (int, float))
                or not isinstance(place_id, str)
            ):
                raise TypeError("baseline place has malformed fields")
            if covers_lonlat(specs[name].target_m, float(lon), float(lat), projection):
                ids.append(place_id)
        baselines[name] = tuple(sorted(set(ids)))
    missing = sorted(set(specs) - set(baselines))
    if missing:
        raise ValueError(f"baseline payload is missing cells: {', '.join(missing)}")
    return baselines


def _in_target_ids(
    places: list[Place], spec: CellSpec, projection: LocalProjection
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                place.place_id
                for place in places
                if covers_lonlat(spec.target_m, place.lon, place.lat, projection)
            }
        )
    )


def run_action(
    spec: CellSpec,
    action: str,
    *,
    projection: LocalProjection,
    fetch: FetchView,
) -> ActionOutcome:
    """Run one action. Failed sub-searches are recorded, never invented."""
    if action == "zoom_in":
        jobs: list[tuple[BaseGeometry, SearchVariant]] = [(spec.rect_m, "probe")]
    elif action == "shift":
        jobs = [(shifted_rect(spec.rect_m), "primary")]
    elif action == "split":
        jobs = [(child, "primary") for child in split_rect(spec.rect_m)]
    else:
        raise ValueError(f"unknown action: {action}")
    places: list[Place] = []
    errors: list[str] = []
    seconds = 0.0
    raw_rows = 0
    for geometry_m, variant in jobs:
        try:
            _view, elapsed, found = fetch(geometry_m, variant)
        except ProviderError as exc:
            errors.append(str(exc))
            continue
        seconds += elapsed
        raw_rows += len(found)
        places.extend(found)
    return ActionOutcome(
        action=action,
        searches=len(jobs),
        seconds=seconds,
        raw_rows=raw_rows,
        in_target_ids=_in_target_ids(places, spec, projection),
        errors=tuple(errors),
    )


def analyse_cell(cell: _CellActions) -> dict[str, object]:
    baseline = set(cell.baseline_ids)
    actions: list[dict[str, object]] = []
    new_sets: list[set[str]] = []
    all_ids = set(baseline)
    for outcome in cell.outcomes:
        found = set(outcome.in_target_ids)
        new_ids = found - baseline
        new_sets.append(new_ids)
        all_ids |= found
        minutes = outcome.seconds / 60.0
        actions.append(
            {
                "action": outcome.action,
                "searches": outcome.searches,
                "seconds": round(outcome.seconds, 2),
                "raw_rows": outcome.raw_rows,
                "in_target": len(found),
                "new_vs_baseline": len(new_ids),
                "new_ids": sorted(new_ids),
                "overlap_with_baseline": len(found & baseline),
                "new_per_search": round(len(new_ids) / outcome.searches, 3),
                "new_per_minute": round(len(new_ids) / minutes, 3) if minutes > 0 else 0.0,
                "errors": list(outcome.errors),
            }
        )
    for index, entry in enumerate(actions):
        others: set[str] = set()
        for other_index, other in enumerate(new_sets):
            if other_index != index:
                others |= other
        entry["new_only_this_action"] = len(new_sets[index] - others)
    return {
        "cell": cell.spec.name,
        "baseline_in_target": len(baseline),
        "baseline_ids": sorted(baseline),
        "actions": actions,
        "union_baseline_and_actions": len(all_ids),
        "extra_over_baseline_all_actions": len(all_ids - baseline),
    }


def run_experiment(
    region: BaseGeometry,
    *,
    area_label: str,
    keyword: str,
    baselines: dict[str, tuple[str, ...]],
    fetch: FetchView,
    actions: tuple[str, ...] = ACTION_NAMES,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    note = progress or (lambda _text: None)
    projection, cells = build_cells(region)
    reports: list[dict[str, object]] = []
    for spec in cells:
        cell = _CellActions(spec=spec, baseline_ids=baselines[spec.name])
        for action in actions:
            note(f"Cell {spec.name} action {action}: starting.")
            outcome = run_action(spec, action, projection=projection, fetch=fetch)
            cell.outcomes.append(outcome)
            note(
                f"Cell {spec.name} action {action}: {outcome.raw_rows} raw rows, "
                f"{len(outcome.in_target_ids)} in-target, "
                f"{outcome.seconds:.0f}s, {len(outcome.errors)} failed searches."
            )
        reports.append(analyse_cell(cell))
    totals: dict[str, object] = {}
    for action in actions:
        entries: list[dict[str, object]] = []
        for report in reports:
            acts = report["actions"]
            assert isinstance(acts, list)
            for entry in acts:
                assert isinstance(entry, dict)
                if entry.get("action") == action:
                    entries.append(entry)
        totals[action] = {
            "searches": sum(_int_value(entry["searches"]) for entry in entries),
            "seconds": round(sum(_float_value(entry["seconds"]) for entry in entries), 2),
            "raw_rows": sum(_int_value(entry["raw_rows"]) for entry in entries),
            "new_vs_baseline": sum(_int_value(entry["new_vs_baseline"]) for entry in entries),
        }
    return {
        "experiment": "exp2-actions",
        "area": area_label,
        "keyword": keyword,
        "baseline": "Experiment 1 first repeat per cell (one ordinary search)",
        "cells": reports,
        "action_totals": totals,
        "claim": CLAIM,
    }


def render_text(payload: dict[str, object]) -> str:
    lines = [
        f"Experiment 2 actions: {payload['area']}",
        f"Keyword: {payload['keyword']!r}. Baseline: {payload['baseline']}.",
        "Actions are judged on new in-target IDs, not raw rows.",
        "",
    ]
    cells = payload["cells"]
    assert isinstance(cells, list)
    for report in cells:
        assert isinstance(report, dict)
        lines.append(
            f"Cell {report['cell']}: baseline {report['baseline_in_target']} in-target IDs. "
            f"Union with all actions {report['union_baseline_and_actions']} "
            f"(+{report['extra_over_baseline_all_actions']})."
        )
        entries = report["actions"]
        assert isinstance(entries, list)
        for entry in entries:
            assert isinstance(entry, dict)
            lines.append(
                f"  {entry['action']}: {entry['searches']} searches, "
                f"raw {entry['raw_rows']}, in-target {entry['in_target']}, "
                f"new {entry['new_vs_baseline']} ({entry['new_per_search']}/search, "
                f"{entry['new_per_minute']}/min), {entry['seconds']}s"
                + (f", errors {entry['errors']}" if entry["errors"] else "")
            )
    totals = payload["action_totals"]
    assert isinstance(totals, dict)
    lines.append("")
    for action, total in totals.items():
        assert isinstance(total, dict)
        lines.append(
            f"Total {action}: {total['searches']} searches, "
            f"{total['new_vs_baseline']} new in-target IDs, {total['seconds']}s."
        )
    lines.append(str(payload["claim"]))
    return "\n".join(lines) + "\n"


def make_live_fetch(
    *,
    keyword: str,
    projection: LocalProjection,
    runner: WorkspaceRunner,
) -> FetchView:
    def fetch(geometry_m: BaseGeometry, variant: SearchVariant) -> tuple[MapView, float, list[Place]]:
        # Fresh adapter per view: its cache must not turn an action into a replay.
        search = GosomSearch(keyword=keyword, projection=projection, fetcher=runner)
        places = search.search(geometry_m, variant=variant)
        record = search.fetches[-1]
        return MapView(lat=record.lat, lon=record.lon, zoom=record.zoom), record.seconds, places

    return fetch


def planned_searches(cells: list[CellSpec], actions: tuple[str, ...]) -> int:
    per_cell = sum(4 if action == "split" else 1 for action in actions)
    return len(cells) * per_cell


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Experiment 2: compare zoom-in, centre-shift, and split against "
            "the Experiment 1 Vashi baselines. Calls Google Maps unless --dry-plan."
        )
    )
    parser.add_argument("--lat", type=float, default=VASHI_LAT)
    parser.add_argument("--lon", type=float, default=VASHI_LON)
    parser.add_argument("--radius-km", type=float, default=DEFAULT_RADIUS_KM)
    parser.add_argument("--area-label", default="Vashi")
    parser.add_argument("--keyword", default="restaurant")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=1200.0)
    parser.add_argument("--image", default="gosom/google-maps-scraper:latest")
    parser.add_argument("--binary", default="")
    parser.add_argument("--proxies-file", type=Path)
    parser.add_argument("--lang", default="en")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--dry-plan", action="store_true", help="Print the plan only. No Google calls.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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
    if not args.baseline.is_file():
        parser.error(f"baseline payload is missing: {args.baseline}")
    stored = json.loads(args.baseline.read_text(encoding="utf-8"))
    if not isinstance(stored, dict):
        parser.error("baseline payload is not a JSON object")
    try:
        baselines = load_baselines(stored, cells, projection)
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    total = planned_searches(cells, ACTION_NAMES)
    if args.dry_plan:
        print(f"Dry plan for {area_label}. Keyword {keyword!r}. No Google calls.")
        for spec in cells:
            base_view = view_for_geometry(spec.rect_m, projection)
            zoom_view = view_for_geometry(spec.rect_m, projection, zoom_delta=1)
            shift_view = view_for_geometry(shifted_rect(spec.rect_m), projection)
            print(
                f"Cell {spec.name}: baseline {len(baselines[spec.name])} in-target IDs "
                f"(view lat={base_view.lat:.6f} lon={base_view.lon:.6f} zoom={base_view.zoom}); "
                f"zoom_in zoom={zoom_view.zoom}; "
                f"shift centre lat={shift_view.lat:.6f} lon={shift_view.lon:.6f} zoom={shift_view.zoom}; "
                f"split into 4 children."
            )
        print(f"Total fresh searches planned: {total} (zoom/shift 1 each, split 4, per cell)")
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
        fetch_budget=total,
        on_event=lambda text: print(text, file=sys.stderr, flush=True),
    )
    fetch = make_live_fetch(keyword=keyword, projection=projection, runner=runner)
    print(
        f"Calling Google Maps {total} times for {area_label}. "
        "Each full page can take a few minutes.",
        file=sys.stderr,
    )
    payload = run_experiment(
        region,
        area_label=area_label,
        keyword=keyword,
        baselines=baselines,
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
