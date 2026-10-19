"""A local session for watching the area split.

Pass a point and a radius, or run the offline demo. The command prints each
piece, the rows that came back, and why that piece was quartered or stopped.
It also writes an HTML file you can open in a browser.
"""

from __future__ import annotations

import argparse
import html
import sys
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from geocoverage.compare import SearchComparison, compare_searches
from geocoverage.fixtures import urban_500
from geocoverage.geometry import circle_region, local_projection
from geocoverage.gosom import GosomSearch, WorkspaceRunner, validate_keyword
from geocoverage.live import load_geojson_region
from geocoverage.planner import SATURATION_FRACTION, CellTrace
from geocoverage.search import MockSearch, Place

DEFAULT_HTML = Path(__file__).resolve().parents[2] / ".runs" / "session.html"
QUADRANTS = {"0": "SW", "1": "SE", "2": "NW", "3": "NE"}


@dataclass(frozen=True)
class SessionView:
    heading: str
    mode: str
    keyword: str
    single_search_in_polygon: int
    planner_in_polygon: int
    only_planner: int
    status: str
    claim: str
    cap: int
    max_searches: int
    min_cell_m: float
    cells: tuple[CellTrace, ...]
    region: BaseGeometry


def piece_name(path: str) -> str:
    if path == "0":
        return "whole area"
    parts = [QUADRANTS.get(char, char) for char in path[1:]]
    return " / ".join(parts)


def _place_line(place: Place) -> str:
    name = place.label or place.place_id
    if place.label and place.label != place.place_id:
        name = f"{place.label}  {place.place_id}"
    return f"{name}  {place.lat:.5f}, {place.lon:.5f}"


def render_text(view: SessionView) -> str:
    split_at = SATURATION_FRACTION * view.cap
    lines = [
        view.heading,
        f"Mode: {view.mode}",
        f"Keyword: {view.keyword or '(demo list)'}",
        f"Planner search limit: {view.max_searches}",
        (
            f"A page is quartered when it returns at least {split_at:.0f} "
            f"of {view.cap} rows. A shorter page stops. The run also stops when "
            f"the search limit is hit or a piece would be smaller than "
            f"{view.min_cell_m:.0f} m."
        ),
        "",
        f"One search of the whole area: {view.single_search_in_polygon} place IDs inside the area.",
        f"Planner: {view.planner_in_polygon} place IDs inside the area.",
        f"IDs that appeared only after splitting: {view.only_planner}",
        f"Status: {view.status}",
        view.claim,
        "",
        "Pieces",
        "------",
    ]
    for cell in view.cells:
        indent = "  " * cell.depth
        lines.append(f"{indent}{piece_name(cell.path)}  {cell.decision}")
        lines.append(
            f"{indent}  rows {cell.rows}, inside {cell.in_polygon}, "
            f"outside {cell.outside}, new IDs {cell.new_ids}"
        )
        lines.append(f"{indent}  {cell.detail}")
        shown = cell.places[:8]
        for place in shown:
            lines.append(f"{indent}  - {_place_line(place)}")
        hidden = len(cell.places) - len(shown)
        if hidden > 0:
            lines.append(f"{indent}  - {hidden} more in the HTML file")
    return "\n".join(lines) + "\n"


def _svg(view: SessionView) -> str:
    minx, miny, maxx, maxy = view.region.bounds
    span_x = max(maxx - minx, 1e-9)
    span_y = max(maxy - miny, 1e-9)
    pad_x = span_x * 0.08
    pad_y = span_y * 0.08
    minx -= pad_x
    maxx += pad_x
    miny -= pad_y
    maxy += pad_y
    span_x = maxx - minx
    span_y = maxy - miny
    width = 760
    height = max(320, int(width * span_y / span_x))

    def xy(lon: float, lat: float) -> tuple[float, float]:
        x = (lon - minx) / span_x * width
        y = (maxy - lat) / span_y * height
        return x, y

    colors = {
        "split": "#c45c26",
        "stop": "#2f6f4e",
        "budget": "#8a8f98",
        "too_small": "#8a5a00",
        "fetch_failed": "#8b2e2e",
        "ranking_risk": "#6b3fa0",
    }
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">',
        "<title>Searched pieces</title>",
    ]
    ring = view.region.exterior.coords if isinstance(view.region, Polygon) else ()
    if ring:
        points = " ".join(f"{xy(lon, lat)[0]:.1f},{xy(lon, lat)[1]:.1f}" for lon, lat in ring)
        parts.append(
            f'<polygon points="{points}" fill="#f4f1ea" '
            'stroke="#1f1d1a" stroke-width="2"/>'
        )
    for cell in view.cells:
        x0, y0 = xy(cell.west, cell.north)
        x1, y1 = xy(cell.east, cell.south)
        color = colors.get(cell.decision, "#333")
        parts.append(
            f'<rect x="{min(x0, x1):.1f}" y="{min(y0, y1):.1f}" '
            f'width="{abs(x1 - x0):.1f}" height="{abs(y1 - y0):.1f}" '
            f'fill="{color}" fill-opacity="0.25" stroke="{color}" stroke-width="2"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def render_html(view: SessionView) -> str:
    sections: list[str] = []
    for cell in view.cells:
        rows = []
        for place in cell.places:
            name = html.escape(place.label or place.place_id)
            place_id = html.escape(place.place_id)
            link = ""
            if view.mode == "live":
                href = html.escape(
                    "https://www.google.com/maps/search/?api=1"
                    f"&query={place.lat:.6f},{place.lon:.6f}"
                    f"&query_place_id={place.place_id}",
                    quote=True,
                )
                link = f' <a href="{href}">map</a>'
            rows.append(
                "<li>"
                f"{name} <code>{place_id}</code> "
                f"{place.lat:.5f}, {place.lon:.5f}{link}"
                "</li>"
            )
        body = "<ul>" + "".join(rows) + "</ul>" if rows else "<p>No in-area places on this page.</p>"
        sections.append(
            "<section>"
            f"<h2>{html.escape(piece_name(cell.path))} "
            f"<span class='tag'>{html.escape(cell.decision)}</span></h2>"
            f"<p>{html.escape(cell.detail)}</p>"
            f"<p>Rows {cell.rows}. Inside {cell.in_polygon}. "
            f"Outside {cell.outside}. New IDs {cell.new_ids}.</p>"
            f"{body}"
            "</section>"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Coverage session</title>
<style>
body {{ font: 16px/1.45 Georgia, serif; margin: 2rem auto; max-width: 920px; color: #1f1d1a; }}
code {{ font-family: ui-monospace, monospace; font-size: 0.85em; }}
.tag {{ font-family: ui-monospace, monospace; font-size: 0.75em; border: 1px solid #1f1d1a; padding: 0.1em 0.4em; }}
section {{ border-top: 1px solid #ddd; padding-top: 0.5rem; }}
</style>
</head>
<body>
<h1>{html.escape(view.heading)}</h1>
<p>{html.escape(view.claim)}</p>
<p>One search kept {view.single_search_in_polygon} place IDs inside the area.
The planner kept {view.planner_in_polygon}. {view.only_planner} appeared only after splitting.
Status: {html.escape(view.status)}.</p>
{_svg(view)}
<p>Orange pieces were quartered. Green pieces stopped because the page was short.
Grey pieces were not searched because the search limit was reached.</p>
{"".join(sections)}
</body>
</html>
"""


def _view_from_comparison(
    *,
    heading: str,
    mode: str,
    keyword: str,
    cap: int,
    max_searches: int,
    min_cell_m: float,
    region: BaseGeometry,
    comparison: SearchComparison,
) -> SessionView:
    report = comparison.planner_report
    return SessionView(
        heading=heading,
        mode=mode,
        keyword=keyword,
        single_search_in_polygon=comparison.single_search_in_polygon,
        planner_in_polygon=comparison.planner_in_polygon,
        only_planner=len(comparison.only_planner),
        status=report.status,
        claim=comparison.claim,
        cap=cap,
        max_searches=max_searches,
        min_cell_m=min_cell_m,
        cells=report.cells,
        region=region,
    )


def run_demo(*, max_searches: int, min_cell_m: float) -> SessionView:
    region, places = urban_500()
    search = MockSearch(places, cap=120, projection=local_projection(region))
    comparison = compare_searches(
        region,
        search,
        max_searches=max_searches,
        min_cell_m=min_cell_m,
        probe_fraction=0,
    )
    return _view_from_comparison(
        heading="Demo on a fake square of 500 places. This does not call Google.",
        mode="demo",
        keyword="",
        cap=search.cap,
        max_searches=max_searches,
        min_cell_m=min_cell_m,
        region=region,
        comparison=comparison,
    )


def run_live(
    region: BaseGeometry,
    *,
    keyword: str,
    max_searches: int,
    min_cell_m: float,
    depth: int,
    timeout_s: float,
    image: str,
    binary: str,
    proxies_file: Path | None,
    lang: str,
    exit_on_inactivity: str,
    area_label: str = "",
) -> SessionView:
    runner = WorkspaceRunner(
        mode="binary" if binary else "docker",
        binary=tuple(binary.split()) if binary else (),
        image=image,
        depth=depth,
        lang=lang,
        timeout_s=timeout_s,
        exit_on_inactivity=exit_on_inactivity or None,
        proxies_file=proxies_file,
        fetch_budget=max_searches + 1,
    )
    projection = local_projection(region)
    search = GosomSearch(keyword=keyword, projection=projection, fetcher=runner)
    comparison = compare_searches(
        region,
        search,
        max_searches=max_searches,
        min_cell_m=min_cell_m,
        probe_fraction=0,
    )
    heading = f"Live Google Maps search for {keyword!r}."
    if area_label:
        heading = f"{heading} Area: {area_label}."
    return _view_from_comparison(
        heading=heading,
        mode="live",
        keyword=keyword,
        cap=search.cap,
        max_searches=max_searches,
        min_cell_m=min_cell_m,
        region=region,
        comparison=comparison,
    )


def view_payload(view: SessionView) -> dict[str, object]:
    """JSON for the local viewer. Place rows are id, label, and coordinates."""
    polygons: list[Polygon] = []
    if isinstance(view.region, Polygon):
        polygons = [view.region]
    elif isinstance(view.region, MultiPolygon):
        polygons = list(view.region.geoms)
    rings = [
        [[float(lon), float(lat)] for lon, lat in polygon.exterior.coords]
        for polygon in polygons
    ]
    cells: list[dict[str, object]] = []
    for cell in view.cells:
        places: list[dict[str, object]] = [
            {
                "place_id": place.place_id,
                "label": place.label,
                "lat": place.lat,
                "lon": place.lon,
                "rank": place.rank,
            }
            for place in cell.places
        ]
        cells.append(
            {
                "path": cell.path,
                "name": piece_name(cell.path),
                "depth": cell.depth,
                "decision": cell.decision,
                "detail": cell.detail,
                "rows": cell.rows,
                "in_polygon": cell.in_polygon,
                "outside": cell.outside,
                "new_ids": cell.new_ids,
                "west": cell.west,
                "south": cell.south,
                "east": cell.east,
                "north": cell.north,
                "places": places,
            }
        )
    return {
        "heading": view.heading,
        "mode": view.mode,
        "keyword": view.keyword,
        "single_search_in_polygon": view.single_search_in_polygon,
        "planner_in_polygon": view.planner_in_polygon,
        "only_planner": view.only_planner,
        "status": view.status,
        "claim": view.claim,
        "cap": view.cap,
        "split_at": round(SATURATION_FRACTION * view.cap),
        "max_searches": view.max_searches,
        "min_cell_m": view.min_cell_m,
        "rings": rings,
        "cells": cells,
    }


def write_session(view: SessionView, html_path: Path) -> None:
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_html(view), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Watch a point-and-radius search split into smaller map views. "
            "Use --demo to try the splitter with no Google calls. "
            "For the map in a browser, run python -m geocoverage.ui."
        )
    )
    parser.add_argument("--demo", action="store_true", help="Fake 500-place square. No Google calls.")
    parser.add_argument("--lat", type=float, help="Center latitude, for example 51.514")
    parser.add_argument("--lon", type=float, help="Center longitude, for example -0.1325")
    parser.add_argument("--radius-km", type=float, help="Circle radius in kilometres, at most 20")
    parser.add_argument("--keyword", default="", help="Search phrase, for example restaurant")
    parser.add_argument("--polygon", type=Path, help="GeoJSON polygon instead of a circle")
    parser.add_argument("--max-searches", type=int)
    parser.add_argument("--min-cell-m", type=float, default=200.0)
    parser.add_argument("--depth", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=1200.0)
    parser.add_argument("--image", default="gosom/google-maps-scraper:latest")
    parser.add_argument("--binary", default="")
    parser.add_argument("--proxies-file", type=Path)
    parser.add_argument("--lang", default="en")
    parser.add_argument("--exit-on-inactivity", default="")
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    limit = args.max_searches
    if limit is None:
        limit = 30 if args.demo else 5
    if limit < 1:
        parser.error("max-searches must be at least 1")
    if args.demo:
        view = run_demo(max_searches=limit, min_cell_m=args.min_cell_m)
    else:
        if args.polygon is not None:
            region = load_geojson_region(args.polygon)
            heading_bits = str(args.polygon)
        elif args.lat is not None or args.lon is not None or args.radius_km is not None:
            if args.lat is None or args.lon is None or args.radius_km is None:
                parser.error("a circle needs --lat, --lon, and --radius-km together")
            try:
                region = circle_region(args.lat, args.lon, args.radius_km)
            except ValueError as exc:
                parser.error(str(exc))
            heading_bits = (
                f"circle of {args.radius_km} km around latitude {args.lat}, longitude {args.lon}"
            )
        else:
            parser.error("pass --demo, or --lat/--lon/--radius-km, or --polygon")
        try:
            keyword = validate_keyword(args.keyword)
        except ValueError as exc:
            parser.error(str(exc))
        if args.proxies_file is not None and not args.proxies_file.is_file():
            parser.error("proxies file is missing")
        print(
            f"Calling Google Maps for {keyword!r} on {heading_bits}. "
            f"Up to {limit + 1} browser runs. Each full page can take a few minutes.",
            file=sys.stderr,
        )
        view = run_live(
            region,
            keyword=keyword,
            max_searches=limit,
            min_cell_m=args.min_cell_m,
            depth=args.depth,
            timeout_s=args.timeout_seconds,
            image=args.image,
            binary=args.binary,
            proxies_file=args.proxies_file,
            lang=args.lang,
            exit_on_inactivity=args.exit_on_inactivity,
            area_label=heading_bits,
        )
    text = render_text(view)
    sys.stdout.write(text)
    write_session(view, args.html)
    print(f"Open {args.html.resolve()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
