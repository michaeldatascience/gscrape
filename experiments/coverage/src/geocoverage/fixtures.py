"""Deterministic worlds used by the coverage tests."""

from __future__ import annotations

from shapely.geometry import MultiPolygon, Polygon, box

from geocoverage.geometry import local_projection, root_rect, split_rect
from geocoverage.search import Place


def square_region() -> Polygon:
    """About 2.2 km on a side at the equator."""
    return box(0.0, 0.0, 0.02, 0.02)


def _grid(
    lon0: float,
    lon1: float,
    lat0: float,
    lat1: float,
    *,
    columns: int,
    rows: int,
    prefix: str,
    rank_start: int,
) -> list[Place]:
    places: list[Place] = []
    index = 0
    for row in range(rows):
        for column in range(columns):
            lon = lon0 + (column + 0.5) * (lon1 - lon0) / columns
            lat = lat0 + (row + 0.5) * (lat1 - lat0) / rows
            places.append(
                Place(
                    place_id=f"{prefix}-{index:04d}",
                    lon=lon,
                    lat=lat,
                    rank=rank_start + index,
                )
            )
            index += 1
    return places


def urban_500() -> tuple[Polygon, list[Place]]:
    """500 places, 125 in each quadrant, inset from the split lines."""
    region = square_region()
    quadrants = (
        (0.0008, 0.0092, 0.0008, 0.0092, "sw"),
        (0.0108, 0.0192, 0.0008, 0.0092, "se"),
        (0.0008, 0.0092, 0.0108, 0.0192, "nw"),
        (0.0108, 0.0192, 0.0108, 0.0192, "ne"),
    )
    places: list[Place] = []
    for lon0, lon1, lat0, lat1, name in quadrants:
        places.extend(
            _grid(
                lon0,
                lon1,
                lat0,
                lat1,
                columns=5,
                rows=25,
                prefix=name,
                rank_start=len(places),
            )
        )
    return region, places


def sparse_20() -> tuple[Polygon, list[Place]]:
    region = square_region()
    places = _grid(
        0.002,
        0.018,
        0.002,
        0.018,
        columns=5,
        rows=4,
        prefix="sparse",
        rank_start=0,
    )
    return region, places


def hidden_150() -> tuple[Polygon, list[Place], set[str], set[str]]:
    """150 places. Primary ranking hides 80. A probe can see 40 of those 80."""
    region = square_region()
    places = _grid(
        0.001,
        0.019,
        0.001,
        0.019,
        columns=10,
        rows=15,
        prefix="hidden",
        rank_start=0,
    )
    hidden_ids = {place.place_id for place in places if place.rank >= 70}
    probe_revealed_ids = {place.place_id for place in places if 70 <= place.rank < 110}
    return region, places, hidden_ids, probe_revealed_ids


def holes_and_islands() -> tuple[MultiPolygon, list[Place], set[str], set[str]]:
    outer = box(0.0, 0.0, 0.02, 0.02)
    hole = box(0.008, 0.008, 0.012, 0.012)
    body = Polygon(outer.exterior.coords, [list(hole.exterior.coords)])
    island_a = box(0.03, 0.0, 0.035, 0.005)
    island_b = box(0.03, 0.01, 0.035, 0.015)
    region = MultiPolygon([body, island_a, island_b])
    included = [
        Place("main", 0.002, 0.002, 0),
        Place("island-a", 0.032, 0.002, 1),
        Place("island-b", 0.032, 0.012, 2),
    ]
    excluded = [
        Place("in-hole", 0.010, 0.010, 3),
        Place("in-gap", 0.025, 0.002, 4),
    ]
    return region, included + excluded, {place.place_id for place in included}, {
        place.place_id for place in excluded
    }


def border_cases() -> tuple[Polygon, list[Place]]:
    """Interior points plus 25 points on the first vertical split."""
    region = square_region()
    projection = local_projection(region)
    region_m = projection.to_meters(region)
    rect = root_rect(region_m)
    minx, miny, maxx, maxy = rect.bounds
    mid_x = (minx + maxx) / 2.0
    mid_y = (miny + maxy) / 2.0
    span_x = (maxx - minx) / 2.0
    span_y = (maxy - miny) / 2.0

    places: list[Place] = []
    rank = 0
    interiors = (
        (minx + 0.25 * span_x, miny + 0.25 * span_y),
        (minx + 0.75 * span_x, miny + 0.25 * span_y),
        (mid_x + 0.25 * span_x, miny + 0.25 * span_y),
        (mid_x + 0.75 * span_x, miny + 0.25 * span_y),
        (minx + 0.25 * span_x, mid_y + 0.25 * span_y),
        (minx + 0.75 * span_x, mid_y + 0.25 * span_y),
        (mid_x + 0.25 * span_x, mid_y + 0.25 * span_y),
        (mid_x + 0.75 * span_x, mid_y + 0.25 * span_y),
    )
    for anchor_x, anchor_y in interiors:
        for step in range(5):
            x = anchor_x + (step - 2) * span_x * 0.05
            y = anchor_y + (step - 2) * span_y * 0.05
            lon, lat = projection.inverse.transform(x, y)
            places.append(Place(f"in-{rank:02d}", lon, lat, rank))
            rank += 1

    # Avoid the horizontal midline so a point is shared by left and right only.
    for step in range(25):
        if step < 13:
            y = miny + (step + 1) * (mid_y - miny) / 14.0
        else:
            y = mid_y + (step - 12) * (maxy - mid_y) / 14.0
        lon, lat = projection.inverse.transform(mid_x, y)
        places.append(Place(f"edge-{step:02d}", lon, lat, rank))
        rank += 1

    # Touch the split helper so a future reorder of children is noticed in review.
    assert len(split_rect(rect)) == 4
    return region, places


def l_shape_with_outsider() -> tuple[Polygon, list[Place], str]:
    """An L polygon. One place sits in the bounding square but outside the L."""
    polygon = Polygon(
        [
            (0.0, 0.0),
            (0.02, 0.0),
            (0.02, 0.01),
            (0.01, 0.01),
            (0.01, 0.02),
            (0.0, 0.02),
            (0.0, 0.0),
        ]
    )
    inside = _grid(
        0.001,
        0.008,
        0.001,
        0.018,
        columns=4,
        rows=5,
        prefix="inside",
        rank_start=0,
    )
    outsider = Place("outside-corner", 0.015, 0.015, rank=10_000)
    return polygon, [*inside, outsider], outsider.place_id
