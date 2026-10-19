from geocoverage.compare import compare_searches
from geocoverage.fixtures import l_shape_with_outsider, urban_500
from geocoverage.geometry import local_projection
from geocoverage.search import MockSearch


def test_planner_finds_the_ids_one_search_drops() -> None:
    region, places = urban_500()
    search = MockSearch(places, cap=120, projection=local_projection(region))
    comparison = compare_searches(
        region,
        search,
        max_searches=1000,
        probe_fraction=1.0,
        seed=4,
    )
    assert comparison.single_search_in_polygon == 120
    assert comparison.planner_in_polygon == 500
    assert len(comparison.overlap) == 120
    assert comparison.only_single_search == ()
    assert len(comparison.only_planner) == 380
    assert comparison.out_of_polygon_single_search == 0
    assert comparison.out_of_polygon_planner == 0
    assert "%" not in comparison.claim
    assert "total available places unknown" in comparison.claim


def test_bounding_view_outsider_is_counted_and_excluded() -> None:
    region, places, outsider_id = l_shape_with_outsider()
    search = MockSearch(places, cap=120, projection=local_projection(region))
    comparison = compare_searches(region, search, max_searches=50, probe_fraction=0)
    assert comparison.out_of_polygon_single_search == 1
    assert outsider_id not in comparison.planner_report.in_polygon_ids
    assert outsider_id not in comparison.overlap
    assert outsider_id not in comparison.only_planner
