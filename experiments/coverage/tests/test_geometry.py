from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from geocoverage.fixtures import square_region
from geocoverage.geometry import (
    InvalidGeometryError,
    area_m2,
    covers_lonlat,
    local_projection,
    polygonal,
    require_region,
    root_rect,
    split_rect,
)


def test_area_is_square_meters_not_degrees() -> None:
    region = box(0.0, 0.0, 0.01, 0.01)
    area = area_m2(region)
    assert area > 1_000_000
    assert area < 1_300_000


def test_longitude_is_not_read_as_latitude() -> None:
    region = box(9.9, 0.9, 10.1, 1.1)
    projection = local_projection(region)
    region_m = projection.to_meters(region)
    assert covers_lonlat(region_m, 10.0, 1.0, projection)
    assert not covers_lonlat(region_m, 1.0, 10.0, projection)


def test_child_union_covers_parent_and_preserves_a_hole() -> None:
    outer = box(0.0, 0.0, 0.02, 0.02)
    hole = box(0.008, 0.008, 0.012, 0.012)
    region = Polygon(outer.exterior.coords, [list(hole.exterior.coords)])
    projection = local_projection(region)
    region_m = polygonal(projection.to_meters(region))
    children = []
    for rect in split_rect(root_rect(region_m)):
        piece = polygonal(rect.intersection(region_m))
        if not piece.is_empty:
            children.append(piece)
    covered = unary_union(children)
    missing = region_m.difference(covered).area
    extra = covered.difference(region_m).area
    assert missing / region_m.area < 1e-9
    assert extra / region_m.area < 1e-9
    hole_point = projection.point_meters(0.010, 0.010)
    assert not covered.covers(hole_point)
    assert region_m.covers(projection.point_meters(0.002, 0.002))


def test_invalid_region_is_rejected() -> None:
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
    assert not bowtie.is_valid
    try:
        require_region(bowtie)
    except InvalidGeometryError:
        return
    raise AssertionError("invalid geometry was accepted")


def test_square_fixture_contains_its_center() -> None:
    region = square_region()
    assert region.covers(Point(0.01, 0.01))
