"""Search adapters. The mock stands in for one map viewport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from shapely.geometry.base import BaseGeometry

from geocoverage.geometry import LocalProjection, covers_lonlat

SearchVariant = Literal["primary", "probe"]


@dataclass(frozen=True)
class Place:
    place_id: str
    lon: float
    lat: float
    rank: int


class ProviderError(RuntimeError):
    """The search adapter failed. Callers record the cell; they do not invent rows."""


class SearchAdapter(Protocol):
    cap: int
    projection: LocalProjection

    def search(self, geometry_m: BaseGeometry, *, variant: SearchVariant = "primary") -> list[Place]:
        """Return this view's ranked slice. Lower ``rank`` is returned first."""


class MockSearch:
    """A fixed world of places and a hard per-view cap.

    ``capped`` returns every place in the view, truncated at ``cap``.
    ``hidden`` drops a seeded subset from the primary ranking even when the
    page is shorter than the cap. The probe variant can reveal some of those
    IDs and never reveals ``permanently_hidden_ids``.
    """

    def __init__(
        self,
        places: list[Place],
        *,
        cap: int,
        projection: LocalProjection,
        mode: Literal["capped", "hidden"] = "capped",
        hidden_ids: set[str] | None = None,
        probe_revealed_ids: set[str] | None = None,
    ) -> None:
        if cap < 1:
            raise ValueError("cap must be at least 1")
        self.places = list(places)
        self.cap = cap
        self.projection = projection
        self.mode = mode
        self.hidden_ids = set(hidden_ids or ())
        self.probe_revealed_ids = set(probe_revealed_ids or ())
        if not self.probe_revealed_ids <= self.hidden_ids:
            raise ValueError("probe_revealed_ids must be a subset of hidden_ids")

    def search(
        self,
        geometry_m: BaseGeometry,
        *,
        variant: SearchVariant = "primary",
    ) -> list[Place]:
        inside = [
            place
            for place in self.places
            if covers_lonlat(geometry_m, place.lon, place.lat, self.projection)
        ]
        inside.sort(key=lambda place: (place.rank, place.place_id))
        if self.mode == "hidden":
            blocked = self.hidden_ids
            if variant == "probe":
                blocked = self.hidden_ids - self.probe_revealed_ids
            inside = [place for place in inside if place.place_id not in blocked]
        return inside[: self.cap]
