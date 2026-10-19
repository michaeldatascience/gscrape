import pytest
from shapely.geometry import Polygon

from geocoverage.fixtures import (
    border_cases,
    hidden_150,
    holes_and_islands,
    sparse_20,
    square_region,
    urban_500,
)
from geocoverage.geometry import InvalidGeometryError, local_projection
from geocoverage.planner import discover
from geocoverage.search import MockSearch, Place, ProviderError


def test_urban_500_recovers_every_id_past_the_120_cap() -> None:
    region, places = urban_500()
    search = MockSearch(places, cap=120, projection=local_projection(region))
    report = discover(region, search, max_searches=1000, probe_fraction=1.0, seed=1)
    assert set(report.in_polygon_ids) == {place.place_id for place in places}
    assert len(report.in_polygon_ids) == 500
    assert report.raw_observations >= 500
    assert report.cells_split >= 5
    assert report.status == "HIGH_EVIDENCE"
    assert report.unresolved == ()
    assert "%" not in report.claim
    assert "100" not in report.claim
    assert not hasattr(report, "completeness_percent")


def test_hidden_rank_is_not_reported_as_complete() -> None:
    region, places, hidden_ids, revealed_ids = hidden_150()
    search = MockSearch(
        places,
        cap=120,
        projection=local_projection(region),
        mode="hidden",
        hidden_ids=hidden_ids,
        probe_revealed_ids=revealed_ids,
    )
    report = discover(region, search, max_searches=5000, probe_fraction=1.0, seed=2)
    found = set(report.in_polygon_ids)
    assert report.status == "UNRESOLVED"
    assert any(cell.reason == "RANKING_RISK" for cell in report.unresolved)
    assert len(found) < 150
    permanently_hidden = hidden_ids - revealed_ids
    assert found.isdisjoint(permanently_hidden)
    assert "%" not in report.claim


def test_sparse_20_does_not_recurse() -> None:
    region, places = sparse_20()
    search = MockSearch(places, cap=120, projection=local_projection(region))
    report = discover(region, search, max_searches=100, probe_fraction=1.0, seed=3)
    assert set(report.in_polygon_ids) == {place.place_id for place in places}
    assert report.cells_split == 0
    assert report.cells_searched == 1
    assert report.status == "HIGH_EVIDENCE"


def test_holes_and_islands_follow_the_polygon() -> None:
    region, places, included, excluded = holes_and_islands()
    search = MockSearch(places, cap=120, projection=local_projection(region))
    report = discover(region, search, max_searches=100, probe_fraction=0)
    found = set(report.in_polygon_ids)
    assert found == included
    assert found.isdisjoint(excluded)


def test_border_points_are_emitted_once() -> None:
    region, places = border_cases()
    search = MockSearch(places, cap=50, projection=local_projection(region))
    report = discover(region, search, max_searches=100, probe_fraction=0)
    assert set(report.in_polygon_ids) == {place.place_id for place in places}
    assert len(report.in_polygon_ids) == len(places) == 65
    assert report.raw_observations > len(report.in_polygon_ids)
    assert report.status == "HIGH_EVIDENCE"


def test_budget_stop_keeps_a_partial_id_set() -> None:
    region, places = urban_500()
    search = MockSearch(places, cap=120, projection=local_projection(region))
    report = discover(region, search, max_searches=1, probe_fraction=0)
    assert report.status == "BUDGET_EXHAUSTED"
    assert any(cell.reason == "BUDGET_EXHAUSTED" for cell in report.unresolved)
    assert len(report.in_polygon_ids) == 120
    assert len(report.in_polygon_ids) < 500


def test_saturated_cell_that_cannot_shrink_stays_unresolved() -> None:
    region = square_region()
    places = [
        Place(f"dense-{index}", 0.01, 0.01 + index * 0.00001, index) for index in range(10)
    ]
    search = MockSearch(places, cap=4, projection=local_projection(region))
    report = discover(region, search, max_searches=10, min_cell_m=10_000, probe_fraction=0)
    assert report.status == "UNRESOLVED"
    assert any(cell.reason == "MIN_GEOMETRY" for cell in report.unresolved)
    assert len(report.in_polygon_ids) == 4


def test_provider_error_is_recorded() -> None:
    region, _places = sparse_20()
    projection = local_projection(region)

    class Down:
        cap = 120

        def search(self, geometry_m: object, *, variant: str = "primary") -> list[Place]:
            raise ProviderError("blocked")

    down = Down()
    down.projection = projection
    report = discover(region, down, max_searches=5, probe_fraction=0)
    assert report.status == "UNRESOLVED"
    assert report.in_polygon_ids == ()
    assert any(cell.reason == "PROVIDER_ERROR" for cell in report.unresolved)


def test_invalid_geometry_raises() -> None:
    _region, places = sparse_20()
    search = MockSearch(places, cap=120, projection=local_projection(square_region()))
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
    with pytest.raises(InvalidGeometryError):
        discover(bowtie, search)
