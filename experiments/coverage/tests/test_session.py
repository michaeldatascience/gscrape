from pathlib import Path

import pytest
from shapely.geometry import Point, box

from geocoverage.fixtures import urban_500
from geocoverage.geometry import area_m2, circle_region, local_projection
from geocoverage.planner import CellTrace, discover
from geocoverage.search import MockSearch, Place
from geocoverage.session import SessionView, main, piece_name, render_html, render_text


def test_circle_is_a_ground_radius() -> None:
    region = circle_region(51.5, -0.12, 1.0)
    assert 3.0e6 < area_m2(region) < 3.3e6
    assert region.covers(Point(-0.12, 51.5))
    assert not region.covers(Point(-0.12, 51.52))
    with pytest.raises(ValueError):
        circle_region(51.5, -0.12, 0)
    with pytest.raises(ValueError):
        circle_region(51.5, -0.12, 21)


def test_full_demo_world_splits_then_stops() -> None:
    region, places = urban_500()
    search = MockSearch(places, cap=120, projection=local_projection(region))
    report = discover(region, search, max_searches=1000, probe_fraction=0)
    assert report.cells[0].decision == "split"
    assert report.cells[0].rows == 120
    assert any(cell.decision == "stop" for cell in report.cells)
    assert report.cells_split == 5
    assert report.status == "HIGH_EVIDENCE"
    assert len(report.in_polygon_ids) == 500


def test_one_search_budget_marks_the_unsearched_pieces() -> None:
    region, places = urban_500()
    search = MockSearch(places, cap=120, projection=local_projection(region))
    report = discover(region, search, max_searches=1, probe_fraction=0)
    assert report.cells[0].decision == "split"
    assert any(cell.decision == "budget" for cell in report.cells)
    assert report.status == "BUDGET_EXHAUSTED"


def test_piece_names_follow_the_quadrant_order() -> None:
    assert piece_name("0") == "whole area"
    assert piece_name("01") == "SE"
    assert piece_name("032") == "NE / NW"
    assert piece_name("0032") == "SW / NE / NW"


def test_html_escapes_place_labels() -> None:
    region = box(0.0, 0.0, 0.02, 0.02)
    place = Place("id-1", 0.01, 0.01, 0, label="<script>alert(1)</script>")
    view = SessionView(
        heading="Demo heading",
        mode="demo",
        keyword="",
        single_search_in_polygon=1,
        planner_in_polygon=1,
        only_planner=0,
        status="HIGH_EVIDENCE",
        claim="1 place IDs discovered; total available places unknown",
        cap=120,
        max_searches=1,
        min_cell_m=200,
        cells=(
            CellTrace(
                path="0",
                depth=0,
                decision="stop",
                detail="Stopped.",
                rows=1,
                in_polygon=1,
                outside=0,
                new_ids=1,
                west=0.0,
                south=0.0,
                east=0.02,
                north=0.02,
                places=(place,),
            ),
        ),
        region=region,
    )
    page = render_html(view)
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    text = render_text(view)
    assert "%" not in text
    assert "at least 96 of 120 rows" in text


def test_demo_command_writes_a_local_page(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    html_path = tmp_path / "session.html"
    assert main(["--demo", "--html", str(html_path), "--max-searches", "30"]) == 0
    out = capsys.readouterr().out
    assert "quartered" in out
    assert "total available places unknown" in out
    assert "%" not in out
    page = html_path.read_text(encoding="utf-8")
    assert "whole area" in page
    assert "does not call Google" in page
    assert "Calling Google" not in out


def test_missing_location_exits() -> None:
    with pytest.raises(SystemExit):
        main([])
