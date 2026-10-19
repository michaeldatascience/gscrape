import json
import sys
import textwrap
from pathlib import Path

import pytest
from shapely.geometry import box

from geocoverage.compare import compare_searches
from geocoverage.fixtures import square_region
from geocoverage.geometry import InvalidGeometryError, local_projection
from geocoverage.gosom import (
    FORBIDDEN_FLAGS,
    MAPS_VIEW_CAP,
    GosomSearch,
    MapView,
    WorkspaceRunner,
    assert_one_view,
    docker_run_args,
    ground_span_m,
    one_view_args,
    parse_gosom_places,
    redact,
    validate_keyword,
    view_for_geometry,
    zoom_for_span,
)
from geocoverage.live import load_geojson_region, main, root_docker_command
from geocoverage.planner import discover
from geocoverage.search import Place, ProviderError

POLYGON = Path(__file__).resolve().parents[1] / "regions" / "central_london.geojson"


def _rows(places: list[dict[str, object]]) -> str:
    return "".join(json.dumps(place) + "\n" for place in places)


def _page(prefix: str, count: int, lon: float, lat: float) -> str:
    return _rows(
        [
            {"place_id": f"{prefix}-{index}", "latitude": lat, "longitude": lon, "title": "drop"}
            for index in range(count)
        ]
    )


class _ZoomPages:
    def __init__(self) -> None:
        self.zooms: list[int] = []

    def fetch(self, *, keyword: str, view: MapView) -> str:
        assert keyword == "coffee"
        self.zooms.append(view.zoom)
        if view.zoom <= 15:
            page = _page("root", 119, 0.01, 0.01)
            outside = {
                "place_id": "outside",
                "latitude": 10.0,
                "longitude": 10.0,
                "phone": "+1-555-0100",
            }
            return page + json.dumps(outside) + "\n"
        return _page(f"child-{view.lat:.4f}-{view.lon:.4f}", 100, view.lon, view.lat)


def test_zoom_uses_the_tightest_pane_that_covers_the_span() -> None:
    zoom = zoom_for_span(0.0, 3000)
    assert zoom == 15
    assert ground_span_m(0.0, zoom) >= 3000
    assert ground_span_m(0.0, zoom + 1) < 3000


def test_equator_square_is_one_maps_view() -> None:
    region = square_region()
    projection = local_projection(region)
    region_m = projection.to_meters(region)
    view = view_for_geometry(region_m, projection)
    assert view.zoom == 15
    assert view.lat == pytest.approx(0.01, abs=1e-6)
    assert view.lon == pytest.approx(0.01, abs=1e-6)
    probe = view_for_geometry(region_m, projection, zoom_delta=1)
    assert probe.zoom == 16


def test_one_view_args_put_latitude_first_and_omit_grid_and_fast_mode() -> None:
    view = MapView(lat=51.514, lon=-0.1325, zoom=15)
    args = one_view_args(
        input_path="/work/query.txt",
        results_path="/work/results.json",
        view=view,
        depth=10,
        lang="en",
        exit_on_inactivity="3m",
        proxies_file="/proxies/proxies.txt",
    )
    assert_one_view(args)
    assert args[args.index("-geo") + 1] == "51.514000,-0.132500"
    assert "-zoom" in args
    assert "15" in args
    for flag in FORBIDDEN_FLAGS:
        assert flag not in args
    with pytest.raises(ValueError, match="forbidden"):
        assert_one_view([*args, "-fast-mode"])
    with pytest.raises(ValueError, match="depth"):
        one_view_args(
            input_path="q",
            results_path="r",
            view=view,
            depth=11,
            lang="en",
            exit_on_inactivity="3m",
            proxies_file=None,
        )


def test_docker_command_mounts_the_work_dir_and_proxies_read_only() -> None:
    scraper = one_view_args(
        input_path="/work/query.txt",
        results_path="/work/results.json",
        view=MapView(lat=1.0, lon=2.0, zoom=14),
        depth=1,
        lang="en",
        exit_on_inactivity="3m",
        proxies_file="/proxies/proxies.txt",
    )
    command = docker_run_args(
        image="gosom/google-maps-scraper:latest",
        container_name="geocoverage-gosom-test",
        work_host=Path("/tmp/work"),
        proxies_host=Path("/secrets/gosom-proxies.txt"),
        scraper_args=scraper,
    )
    assert command[0] == "docker"
    assert "gosom/google-maps-scraper:latest" in command
    assert "/tmp/work:/work" in command
    assert "/secrets/gosom-proxies.txt:/proxies/proxies.txt:ro" in command
    assert_one_view(command)


def test_parse_keeps_place_id_and_rank_and_rejects_malformed_rows() -> None:
    places = parse_gosom_places(
        _rows(
            [
                {
                    "place_id": "aaa",
                    "latitude": 51.5,
                    "longtitude": -0.1,
                    "title": "Cafe",
                    "phone": "+44",
                },
                {"place_id": "aaa", "latitude": 51.5, "longitude": -0.1},
                {"place_id": "bbb", "latitude": 51.6, "longitude": -0.2},
            ]
        )
    )
    assert places == [
        Place("aaa", lon=-0.1, lat=51.5, rank=0),
        Place("bbb", lon=-0.2, lat=51.6, rank=2),
    ]
    assert parse_gosom_places("") == []
    assert parse_gosom_places('[{"place_id": "c", "latitude": 1, "longitude": 2}]') == [
        Place("c", lon=2, lat=1, rank=0)
    ]
    with pytest.raises(ProviderError, match="place_id"):
        parse_gosom_places('{"latitude": 1, "longitude": 2}\n')
    with pytest.raises(ProviderError, match="latitude"):
        parse_gosom_places('{"place_id": "x", "longitude": 2}\n')
    with pytest.raises(ProviderError, match="conflicting"):
        parse_gosom_places(
            '{"place_id": "x", "latitude": 1, "longitude": 2, "longtitude": 3}\n'
        )
    with pytest.raises(ProviderError, match="line 1"):
        parse_gosom_places("{not json}\n")


def test_redact_removes_proxy_urls() -> None:
    raw = "dial socks5://user:supersecret@10.1.1.1:1080 failed"
    cleaned = redact(raw)
    assert "supersecret" not in cleaned
    assert "[redacted-url]" in cleaned


def test_london_polygon_is_geometry_and_dry_run_stays_one_view() -> None:
    region = load_geojson_region(POLYGON)
    assert region.geom_type == "Polygon"
    command = root_docker_command(
        region,
        keyword="coffee",
        image="gosom/google-maps-scraper:latest",
        depth=10,
        lang="en",
        exit_on_inactivity="3m",
        proxies_file=None,
    )
    assert_one_view(command)
    geo = command[command.index("-geo") + 1]
    lat_text, lon_text = geo.split(",")
    assert float(lat_text) == pytest.approx(51.514, abs=1e-3)
    assert float(lon_text) == pytest.approx(-0.1325, abs=1e-3)
    assert command[command.index("-zoom") + 1] == "15"


def test_place_name_is_rejected(tmp_path: Path) -> None:
    name_file = tmp_path / "name.json"
    name_file.write_text('"London"', encoding="utf-8")
    with pytest.raises(InvalidGeometryError, match="place name"):
        load_geojson_region(name_file)


def test_dry_run_prints_the_command_without_a_proxy_url(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--keyword", "coffee", "--polygon", str(POLYGON), "--dry-run"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    command = payload["command"]
    assert_one_view(command)
    assert "://" not in " ".join(command)
    with pytest.raises(SystemExit):
        main(["--keyword", "https://maps.google.com/maps?q=coffee", "--dry-run"])


def test_planner_uses_gosom_pages_and_clips_the_polygon() -> None:
    region = square_region()
    fetcher = _ZoomPages()
    search = GosomSearch(
        keyword="coffee",
        projection=local_projection(region),
        fetcher=fetcher,
    )
    assert search.cap == MAPS_VIEW_CAP
    comparison = compare_searches(
        region,
        search,
        max_searches=5,
        probe_fraction=0,
    )
    assert fetcher.zooms[0] == 15
    assert fetcher.zooms.count(15) == 1
    assert comparison.single_search_in_polygon == 119
    assert comparison.out_of_polygon_single_search == 1
    assert "outside" not in comparison.overlap
    assert "outside" not in comparison.only_planner
    assert comparison.only_planner
    assert comparison.planner_report.status == "BUDGET_EXHAUSTED"
    assert "%" not in comparison.claim
    assert "total available places unknown" in comparison.claim


def test_provider_error_from_gosom_is_recorded() -> None:
    region = square_region()

    class Down:
        def fetch(self, *, keyword: str, view: MapView) -> str:
            raise ProviderError("blocked")

    search = GosomSearch(keyword="coffee", projection=local_projection(region), fetcher=Down())
    report = discover(region, search, max_searches=2, probe_fraction=0)
    assert report.status == "UNRESOLVED"
    assert any(cell.reason == "PROVIDER_ERROR" for cell in report.unresolved)
    assert report.in_polygon_ids == ()


def _write_fake(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


def test_subprocess_runner_reads_place_ids_and_redacts_proxy_stderr(tmp_path: Path) -> None:
    secret = "socks5://user:supersecret@10.1.1.1:1080"
    proxies = tmp_path / "proxies.txt"
    proxies.write_text(secret + "\n", encoding="utf-8")
    success = tmp_path / "success.py"
    _write_fake(
        success,
        """
        import json
        import sys

        args = sys.argv[1:]
        forbidden = {
            "-fast-mode",
            "-grid-bbox",
            "-grid-cell",
            "-email",
            "-extra-reviews",
        }
        if forbidden.intersection(args):
            sys.stderr.write("forbidden\\n")
            sys.exit(2)
        geo = args[args.index("-geo") + 1]
        lat, lon = (float(part) for part in geo.split(","))
        results = args[args.index("-results") + 1]
        row = {
            "place_id": "pid-1",
            "latitude": lat,
            "longitude": lon,
            "title": "hidden",
            "phone": "+1",
        }
        with open(results, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\\n")
        """,
    )
    runner = WorkspaceRunner(
        mode="binary",
        binary=(sys.executable, str(success)),
        proxies_file=proxies,
        timeout_s=30,
        depth=1,
        fetch_budget=1,
    )
    view = MapView(lat=51.5, lon=-0.12, zoom=15)
    text = runner.fetch(keyword="coffee", view=view)
    places = parse_gosom_places(text)
    assert places == [Place("pid-1", lon=-0.12, lat=51.5, rank=0)]
    with pytest.raises(ProviderError, match="budget"):
        runner.fetch(keyword="coffee", view=view)

    failure = tmp_path / "failure.py"
    _write_fake(
        failure,
        """
        import pathlib
        import sys

        args = sys.argv[1:]
        proxy_path = args[args.index("-proxies-file") + 1]
        sys.stderr.write(pathlib.Path(proxy_path).read_text(encoding="utf-8"))
        sys.exit(1)
        """,
    )
    noisy = WorkspaceRunner(
        mode="binary",
        binary=(sys.executable, str(failure)),
        proxies_file=proxies,
        timeout_s=30,
        depth=1,
    )
    with pytest.raises(ProviderError) as caught:
        noisy.fetch(keyword="coffee", view=view)
    message = str(caught.value)
    assert "supersecret" not in message
    assert "[redacted-url]" in message


def test_subprocess_timeout_is_a_provider_error(tmp_path: Path) -> None:
    sleeper = tmp_path / "sleep.py"
    _write_fake(
        sleeper,
        """
        import time
        time.sleep(30)
        """,
    )
    runner = WorkspaceRunner(
        mode="binary",
        binary=(sys.executable, str(sleeper)),
        timeout_s=1,
        depth=1,
    )
    with pytest.raises(ProviderError, match="timed out"):
        runner.fetch(keyword="coffee", view=MapView(lat=1.0, lon=2.0, zoom=12))


def test_keyword_must_be_a_phrase() -> None:
    assert validate_keyword(" coffee ") == "coffee"
    with pytest.raises(ValueError, match="maps URL"):
        validate_keyword("https://www.google.com/maps/search/coffee")
    outside = box(0, 0, 1, 1)
    with pytest.raises(ValueError, match="empty"):
        view_for_geometry(box(0, 0, 0, 0), local_projection(outside))
