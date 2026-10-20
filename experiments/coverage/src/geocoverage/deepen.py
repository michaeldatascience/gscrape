"""Experiment 3: does a second level of splitting keep finding places?

One productive cell per locality is taken from Experiment 2. Its baseline is
everything Experiments 1-2 already observed in that cell (first-repeat
baseline plus every action's new IDs). The cell's four children are each
split again and the 16 grandchildren (~187.5 m) are searched once each.
Grandchild returns are clipped to the original cell-and-circle target.

The question is diminishing returns: how many new in-target IDs does level 2
buy, per search, compared with what level 1 (split) bought in Experiment 2?
No completeness claim is made.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from shapely.geometry.base import BaseGeometry

from geocoverage.actions import FetchView, make_live_fetch
from geocoverage.geometry import (
    circle_region,
    covers_lonlat,
    split_rect,
)
from geocoverage.gosom import WorkspaceRunner, validate_keyword, view_for_geometry
from geocoverage.repeatability import CellSpec, build_cells
from geocoverage.search import ProviderError

CHILD_NAMES = ("SW", "SE", "NW", "NE")
CLAIM = (
    "Deepening evidence only. The new union is a larger observed set, "
    "not every place in the cell. Total places unknown."
)


def load_cell_union(stored: dict[str, object], cell_name: str) -> tuple[str, ...]:
    """Baseline plus every action's new IDs for one cell, from Experiment 2."""
    stored_cells = stored.get("cells")
    if not isinstance(stored_cells, list):
        raise TypeError("actions payload has no cells list")
    for report in stored_cells:
        if not isinstance(report, dict):
            raise TypeError("actions cell report is malformed")
        if report.get("cell") != cell_name:
            continue
        ids: set[str] = set()
        baseline = report.get("baseline_ids")
        if not isinstance(baseline, list):
            raise TypeError("actions cell report has no baseline_ids")
        ids.update(str(place_id) for place_id in baseline)
        actions = report.get("actions")
        if not isinstance(actions, list):
            raise TypeError("actions cell report has no actions")
        for entry in actions:
            if not isinstance(entry, dict):
                raise TypeError("actions entry is malformed")
            new_ids = entry.get("new_ids")
            if not isinstance(new_ids, list):
                raise TypeError("actions entry has no new_ids")
            ids.update(str(place_id) for place_id in new_ids)
        return tuple(sorted(ids))
    raise ValueError(f"actions payload has no cell named {cell_name}")


def grandchildren(spec: CellSpec) -> list[tuple[str, BaseGeometry]]:
    """The 16 second-level rectangles of one cell, named like ``SE/NW``."""
    cells: list[tuple[str, BaseGeometry]] = []
    for child_name, child in zip(CHILD_NAMES, split_rect(spec.rect_m), strict=True):
        for grand_name, grand in zip(CHILD_NAMES, split_rect(child), strict=True):
            cells.append((f"{child_name}/{grand_name}", grand))
    return cells


def run_experiment(
    region: BaseGeometry,
    *,
    area_label: str,
    keyword: str,
    cell_name: str,
    baseline_ids: tuple[str, ...],
    fetch: FetchView,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    note = progress or (lambda _text: None)
    projection, cells = build_cells(region)
    specs = {spec.name: spec for spec in cells}
    if cell_name not in specs:
        raise ValueError(f"unknown cell: {cell_name}")
    spec = specs[cell_name]
    baseline = set(baseline_ids)
    seen = set(baseline)
    entries: list[dict[str, object]] = []
    errors: list[str] = []
    for name, rect in grandchildren(spec):
        view = view_for_geometry(rect, projection)
        note(f"Grandchild {name}: opening map page (zoom {view.zoom}).")
        try:
            _view, seconds, places = fetch(rect, "primary")
        except ProviderError as exc:
            errors.append(f"{name}: {exc}")
            note(f"Grandchild {name}: failed ({exc}).")
            continue
        in_target = {
            place.place_id
            for place in places
            if covers_lonlat(spec.target_m, place.lon, place.lat, projection)
        }
        new_ids = in_target - seen
        seen |= in_target
        entries.append(
            {
                "grandchild": name,
                "zoom": view.zoom,
                "seconds": round(seconds, 2),
                "raw_rows": len(places),
                "in_target": len(in_target),
                "new_vs_all_previous": len(new_ids),
                "new_ids": sorted(new_ids),
            }
        )
        note(
            f"Grandchild {name}: {len(places)} raw rows, {len(in_target)} in-target, "
            f"{len(new_ids)} new, {seconds:.0f}s."
        )
    total_new = len(seen - baseline)
    searched = len(entries)
    return {
        "experiment": "exp3-deepen",
        "area": area_label,
        "keyword": keyword,
        "cell": cell_name,
        "baseline_in_target": len(baseline),
        "grandchildren_searched": searched,
        "grandchildren_failed": len(errors),
        "errors": errors,
        "new_in_target_level2": total_new,
        "union_after_level2": len(seen),
        "new_per_search": round(total_new / searched, 3) if searched else 0.0,
        "grandchildren": entries,
        "claim": CLAIM,
    }


def render_text(payload: dict[str, object]) -> str:
    lines = [
        f"Experiment 3 deepen: {payload['area']}, cell {payload['cell']}",
        (
            f"Keyword: {payload['keyword']!r}. Baseline (Experiments 1-2 union): "
            f"{payload['baseline_in_target']} in-target IDs."
        ),
        (
            f"Level-2 split searched {payload['grandchildren_searched']} grandchildren, "
            f"found {payload['new_in_target_level2']} new in-target IDs "
            f"({payload['new_per_search']}/search). Union now {payload['union_after_level2']}."
        ),
        "",
    ]
    entries = payload["grandchildren"]
    assert isinstance(entries, list)
    for entry in entries:
        assert isinstance(entry, dict)
        lines.append(
            f"  {entry['grandchild']}: raw {entry['raw_rows']}, "
            f"in-target {entry['in_target']}, new {entry['new_vs_all_previous']}, "
            f"zoom {entry['zoom']}, {entry['seconds']}s"
        )
    if payload["errors"]:
        lines.append(f"Failed: {payload['errors']}")
    lines.append(str(payload["claim"]))
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Experiment 3: split one Experiment 2 cell a second level and "
            "count new in-target IDs. Calls Google Maps unless --dry-plan."
        )
    )
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--radius-km", type=float, default=0.75)
    parser.add_argument("--area-label", default="")
    parser.add_argument("--keyword", default="restaurant")
    parser.add_argument("--cell", required=True, help="One of SW, SE, NW, NE")
    parser.add_argument("--baseline", type=Path, required=True, help="Experiment 2 payload (.runs JSON)")
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=1200.0)
    parser.add_argument("--image", default="gosom/google-maps-scraper:latest")
    parser.add_argument("--binary", default="")
    parser.add_argument("--proxies-file", type=Path)
    parser.add_argument("--lang", default="en")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dry-plan", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cell not in CHILD_NAMES:
        parser.error("cell must be one of SW, SE, NW, NE")
    try:
        keyword = validate_keyword(args.keyword)
        region = circle_region(args.lat, args.lon, args.radius_km)
    except ValueError as exc:
        parser.error(str(exc))
    if not args.baseline.is_file():
        parser.error(f"baseline payload is missing: {args.baseline}")
    stored = json.loads(args.baseline.read_text(encoding="utf-8"))
    if not isinstance(stored, dict):
        parser.error("baseline payload is not a JSON object")
    try:
        baseline_ids = load_cell_union(stored, args.cell)
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    area_label = args.area_label or f"latitude {args.lat}, longitude {args.lon}"
    projection, cells = build_cells(region)
    specs = {spec.name: spec for spec in cells}
    spec = specs[args.cell]
    grands = grandchildren(spec)
    if args.dry_plan:
        print(
            f"Dry plan for {area_label} cell {args.cell}. Keyword {keyword!r}. "
            f"Baseline {len(baseline_ids)} in-target IDs. No Google calls."
        )
        for name, rect in grands:
            view = view_for_geometry(rect, projection)
            print(f"  {name}: view lat={view.lat:.6f} lon={view.lon:.6f} zoom={view.zoom}")
        print(f"Total fresh searches planned: {len(grands)}")
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
        fetch_budget=len(grands),
        on_event=lambda text: print(text, file=sys.stderr, flush=True),
    )
    fetch = make_live_fetch(keyword=keyword, projection=projection, runner=runner)
    print(
        f"Calling Google Maps {len(grands)} times for {area_label} cell {args.cell}.",
        file=sys.stderr,
    )
    payload = run_experiment(
        region,
        area_label=area_label,
        keyword=keyword,
        cell_name=args.cell,
        baseline_ids=baseline_ids,
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
