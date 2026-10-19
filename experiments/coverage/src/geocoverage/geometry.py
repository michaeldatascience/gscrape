"""Polygon helpers. Areas and splits are computed in meters, never in degrees."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pyproj import Transformer
from shapely import transform
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

# Boundary points can drift by millimeters after a projection round trip.
BOUNDARY_TOLERANCE_M = 0.05


def _reproject(transformer: Transformer, coords: np.ndarray) -> np.ndarray:
    x, y = transformer.transform(coords[:, 0], coords[:, 1])
    return np.column_stack((x, y))


class InvalidGeometryError(ValueError):
    """The requested region is empty, open, or not a polygonal geometry."""


@dataclass(frozen=True)
class LocalProjection:
    forward: Transformer
    inverse: Transformer

    def to_meters(self, geom: BaseGeometry) -> BaseGeometry:
        return transform(geom, lambda coords: _reproject(self.forward, coords))

    def to_wgs84(self, geom: BaseGeometry) -> BaseGeometry:
        return transform(geom, lambda coords: _reproject(self.inverse, coords))

    def point_meters(self, lon: float, lat: float) -> Point:
        x, y = self.forward.transform(lon, lat)
        return Point(x, y)


def require_region(region: BaseGeometry) -> None:
    if region.is_empty or region.geom_type not in {"Polygon", "MultiPolygon"}:
        raise InvalidGeometryError("region must be a non-empty Polygon or MultiPolygon")
    if not region.is_valid:
        raise InvalidGeometryError("region geometry is invalid")


def local_projection(region: BaseGeometry) -> LocalProjection:
    """Azimuthal equidistant meters centered on the region, for this job only."""
    lon = float(region.centroid.x)
    lat = float(region.centroid.y)
    projected = (
        f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs"
    )
    forward = Transformer.from_crs("EPSG:4326", projected, always_xy=True)
    inverse = Transformer.from_crs(projected, "EPSG:4326", always_xy=True)
    return LocalProjection(forward=forward, inverse=inverse)


def area_m2(region: BaseGeometry) -> float:
    require_region(region)
    projected = local_projection(region).to_meters(region)
    return float(projected.area)


def polygonal(geom: BaseGeometry) -> BaseGeometry:
    if geom.is_empty:
        return Polygon()
    if isinstance(geom, (Polygon, MultiPolygon)):
        if isinstance(geom, Polygon) and geom.area == 0:
            return Polygon()
        return geom
    parts: list[BaseGeometry] = []
    for part in getattr(geom, "geoms", ()):
        if isinstance(part, (Polygon, MultiPolygon)) and not part.is_empty and part.area > 0:
            parts.append(part)
    if not parts:
        return Polygon()
    merged = unary_union(parts)
    return merged if not merged.is_empty else Polygon()


def root_rect(region_m: BaseGeometry) -> Polygon:
    minx, miny, maxx, maxy = region_m.bounds
    return box(minx, miny, maxx, maxy)


def split_rect(rect: Polygon) -> tuple[Polygon, Polygon, Polygon, Polygon]:
    """Four children in a stable order: SW, SE, NW, NE."""
    minx, miny, maxx, maxy = rect.bounds
    mid_x = (minx + maxx) / 2.0
    mid_y = (miny + maxy) / 2.0
    return (
        box(minx, miny, mid_x, mid_y),
        box(mid_x, miny, maxx, mid_y),
        box(minx, mid_y, mid_x, maxy),
        box(mid_x, mid_y, maxx, maxy),
    )


def min_side_m(rect: Polygon) -> float:
    minx, miny, maxx, maxy = rect.bounds
    return min(maxx - minx, maxy - miny)


def covers_lonlat(
    geom_m: BaseGeometry,
    lon: float,
    lat: float,
    projection: LocalProjection,
) -> bool:
    point = projection.point_meters(lon, lat)
    if geom_m.covers(point):
        return True
    return geom_m.distance(point) <= BOUNDARY_TOLERANCE_M
