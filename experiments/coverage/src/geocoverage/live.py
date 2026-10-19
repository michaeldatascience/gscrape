"""Compare one gosom view with the planner on a polygon.

The run is opt-in. Tests do not call Google. A hard fetch budget stops the
process starts. The printed claim does not treat the union as every listing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from geocoverage.compare import SearchComparison, compare_searches
from geocoverage.geometry import (
    InvalidGeometryError,
    local_projection,
    polygonal,
    require_region,
    root_rect,
)
from geocoverage.gosom import (
    GosomSearch,
    WorkspaceRunner,
    docker_run_args,
    one_view_args,
    validate_keyword,
    view_for_geometry,
)
from geocoverage.planner import CoverageReport

DEFAULT_POLYGON = (
    Path(__file__).resolve().parents[2] / "regions" / "central_london.geojson"
)
DEFAULT_IMAGE = "gosom/google-maps-scraper:latest"


def load_geojson_region(path: Path) -> BaseGeometry:
    """Load one Polygon or MultiPolygon. A place name is not a region."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InvalidGeometryError(f"region file is not JSON: {exc.msg}") from exc
    if isinstance(payload, str):
        raise InvalidGeometryError("region file must be GeoJSON, not a place name")
    if not isinstance(payload, dict):
        raise InvalidGeometryError("region file must be a GeoJSON object")
    kind = payload.get("type")
    if kind == "FeatureCollection":
        features = payload.get("features")
        if not isinstance(features, list) or len(features) != 1:
            raise InvalidGeometryError("region file must contain one feature")
        feature = features[0]
        if not isinstance(feature, dict):
            raise InvalidGeometryError("region feature is malformed")
        geometry = feature.get("geometry")
    elif kind == "Feature":
        geometry = payload.get("geometry")
    else:
        geometry = payload
    if not isinstance(geometry, dict):
        raise InvalidGeometryError("region geometry is missing")
    try:
        region = shape(geometry)
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidGeometryError("region geometry is malformed") from exc
    require_region(region)
    return region


def root_docker_command(
    region: BaseGeometry,
    *,
    keyword: str,
    image: str,
    depth: int,
    lang: str,
    exit_on_inactivity: str,
    proxies_file: Path | None,
) -> list[str]:
    """The root-cell command. Dry-run prints this and does not start it."""
    projection = local_projection(region)
    region_m = polygonal(projection.to_meters(region))
    view = view_for_geometry(root_rect(region_m), projection)
    proxies_in_container = "/proxies/proxies.txt" if proxies_file is not None else None
    scraper_args = one_view_args(
        input_path="/work/query.txt",
        results_path="/work/results.json",
        view=view,
        depth=depth,
        lang=lang,
        exit_on_inactivity=exit_on_inactivity,
        proxies_file=proxies_in_container,
    )
    return docker_run_args(
        image=image,
        container_name="geocoverage-gosom-dry-run",
        work_host=Path("/tmp/geocoverage-gosom-dry-run"),
        proxies_host=proxies_file,
        scraper_args=scraper_args,
    )


def _unresolved(report: CoverageReport) -> list[dict[str, str]]:
    return [{"path": cell.path, "reason": cell.reason} for cell in report.unresolved]


def comparison_payload(
    *,
    keyword: str,
    polygon: Path,
    image: str,
    max_searches: int,
    search: GosomSearch,
    comparison: SearchComparison,
) -> dict[str, object]:
    report = comparison.planner_report
    started = [record for record in search.fetches if not record.cached]
    return {
        "adapter": "gosom",
        "keyword": keyword,
        "polygon": str(polygon),
        "image": image,
        "cap": search.cap,
        "max_searches": max_searches,
        "gosom_process_starts": search_starts(search),
        "cached_views": sum(1 for record in search.fetches if record.cached),
        "views": [
            {
                "lat": round(record.lat, 6),
                "lon": round(record.lon, 6),
                "zoom": record.zoom,
                "cached": record.cached,
                "seconds": round(record.seconds, 3),
                "rows": record.rows,
            }
            for record in search.fetches
        ],
        "single_search_in_polygon": comparison.single_search_in_polygon,
        "planner_in_polygon": comparison.planner_in_polygon,
        "overlap": len(comparison.overlap),
        "only_single_search": len(comparison.only_single_search),
        "only_planner": len(comparison.only_planner),
        "overlap_ids": list(comparison.overlap),
        "only_single_search_ids": list(comparison.only_single_search),
        "only_planner_ids": list(comparison.only_planner),
        "out_of_polygon_single_search": comparison.out_of_polygon_single_search,
        "out_of_polygon_planner": comparison.out_of_polygon_planner,
        "planner_status": report.status,
        "cells_searched": report.cells_searched,
        "cells_split": report.cells_split,
        "unresolved": _unresolved(report),
        "completeness": "unknown",
        "claim": comparison.claim,
        "fetched_views": len(started),
    }


def search_starts(search: GosomSearch) -> int:
    started = getattr(search.fetcher, "started", None)
    if isinstance(started, int):
        return started
    return sum(1 for record in search.fetches if not record.cached)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare one gosom map view with the cell planner. "
            "The union is an observed sample; completeness stays unknown."
        )
    )
    parser.add_argument("--keyword", required=True)
    parser.add_argument("--polygon", type=Path, default=DEFAULT_POLYGON)
    parser.add_argument("--max-searches", type=int, default=5)
    parser.add_argument("--min-cell-m", type=float, default=200.0)
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=1200.0)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--binary", default="")
    parser.add_argument("--proxies-file", type=Path)
    parser.add_argument("--lang", default="en")
    parser.add_argument(
        "--exit-on-inactivity",
        default="",
        help="Optional gosom inactivity duration, such as 3m. Empty leaves it off.",
    )
    parser.add_argument("--probe-fraction", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the root-cell docker command and do not start a browser.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_searches < 1:
        parser.error("max-searches must be at least 1")
    try:
        validate_keyword(args.keyword)
    except ValueError as exc:
        parser.error(str(exc))
    if args.proxies_file is not None and not args.proxies_file.is_file():
        parser.error("proxies file is missing")
    region = load_geojson_region(args.polygon)
    if args.dry_run:
        command = root_docker_command(
            region,
            keyword=args.keyword,
            image=args.image,
            depth=args.depth,
            lang=args.lang,
            exit_on_inactivity=args.exit_on_inactivity,
            proxies_file=args.proxies_file,
        )
        print(json.dumps({"dry_run": True, "command": command}, indent=2))
        return 0

    if args.binary:
        mode = "binary"
        binary = tuple(args.binary.split())
    else:
        mode = "docker"
        binary = ()
    runner = WorkspaceRunner(
        mode=mode,
        binary=binary,
        image=args.image,
        depth=args.depth,
        lang=args.lang,
        timeout_s=args.timeout_seconds,
        exit_on_inactivity=args.exit_on_inactivity,
        proxies_file=args.proxies_file,
        fetch_budget=args.max_searches,
    )
    projection = local_projection(region)
    search = GosomSearch(keyword=args.keyword, projection=projection, fetcher=runner)
    comparison = compare_searches(
        region,
        search,
        max_searches=args.max_searches,
        min_cell_m=args.min_cell_m,
        probe_fraction=args.probe_fraction,
        seed=args.seed,
    )
    payload = comparison_payload(
        keyword=search.keyword,
        polygon=args.polygon,
        image=args.image if mode == "docker" else args.binary,
        max_searches=args.max_searches,
        search=search,
        comparison=comparison,
    )
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
