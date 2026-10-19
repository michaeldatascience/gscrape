import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from shapely.geometry import box

from geocoverage.planner import CellTrace
from geocoverage.search import Place
from geocoverage.session import SessionView, run_demo, view_payload
from geocoverage.ui import PAGE_HTML, Handler, begin_job, main, plan_listen, wait_job


@pytest.fixture
def viewer_url() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    assert isinstance(port, int)
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_page_is_local_and_builds_text_in_the_dom() -> None:
    assert "Search this area" in PAGE_HTML
    assert "Practice map" in PAGE_HTML
    assert "does not use the latitude" in PAGE_HTML
    assert "Play the split" in PAGE_HTML
    assert "innerHTML" not in PAGE_HTML
    assert "<script src" not in PAGE_HTML
    assert "cdn." not in PAGE_HTML


def test_payload_describes_the_demo_split() -> None:
    payload = view_payload(run_demo(max_searches=30, min_cell_m=200))
    assert payload["single_search_in_polygon"] == 120
    assert payload["planner_in_polygon"] == 500
    assert payload["only_planner"] == 380
    assert payload["split_at"] == 96
    assert payload["status"] == "HIGH_EVIDENCE"
    claim = payload["claim"]
    assert isinstance(claim, str)
    assert "unknown" in claim
    assert "%" not in claim
    cells = payload["cells"]
    assert isinstance(cells, list)
    assert cells[0]["decision"] == "split"
    assert any(cell["decision"] == "stop" for cell in cells)
    rings = payload["rings"]
    assert isinstance(rings, list)
    assert rings and rings[0][0] == rings[0][-1]


def test_payload_keeps_a_label_as_data() -> None:
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
        region=box(0.0, 0.0, 0.02, 0.02),
    )
    encoded = json.dumps(view_payload(view))
    assert "<script>" in encoded
    assert "phone" not in encoded


def test_demo_endpoint_returns_the_split(viewer_url: str) -> None:
    page = urlopen(viewer_url + "/", timeout=5)
    assert page.status == 200
    assert b"Search this area" in page.read()
    with urlopen(viewer_url + "/api/demo?max_searches=30", timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert payload["planner_in_polygon"] == 500
    assert payload["status"] == "HIGH_EVIDENCE"
    with urlopen(viewer_url + "/api/demo?max_searches=1", timeout=10) as response:
        short = json.loads(response.read().decode("utf-8"))
    assert short["status"] == "BUDGET_EXHAUSTED"
    assert any(cell["decision"] == "budget" for cell in short["cells"])


def test_live_form_is_rejected_before_a_scrape(viewer_url: str) -> None:
    cases = [
        {"lat": 51.5, "lon": -0.12, "radius_km": 50, "keyword": "restaurant", "max_searches": 1},
        {
            "lat": 51.5,
            "lon": -0.12,
            "radius_km": 0.5,
            "keyword": "https://maps.example/maps",
            "max_searches": 1,
        },
        {"lat": 51.5, "lon": -0.12, "radius_km": 0.5, "keyword": "restaurant", "max_searches": 99},
    ]
    for body in cases:
        request = Request(
            viewer_url + "/api/live",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as caught:
            urlopen(request, timeout=5)
        assert caught.value.code == 400
        payload = json.loads(caught.value.read().decode("utf-8"))
        assert payload["error"]


def test_status_starts_idle(viewer_url: str) -> None:
    with urlopen(viewer_url + "/api/status", timeout=2) as response:
        body = json.loads(response.read().decode("utf-8"))
    assert body["state"] == "idle"


def test_running_search_is_shown_instead_of_an_error(
    viewer_url: str, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()
    log_path = tmp_path / "viewer.log"
    monkeypatch.setattr("geocoverage.ui._LOG_FILE", log_path)

    def work(progress: object) -> SessionView:
        assert callable(progress)
        progress("Opening map page 1.")
        started.set()
        release.wait(5)
        return run_demo(max_searches=1, min_cell_m=200)

    try:
        summary = (
            "Google Maps search for 'restaurant' within 0.9 km "
            "of latitude 19.0164, longitude 73.0373."
        )
        begin_job("live", summary, work)
        assert started.wait(2)
        err = capsys.readouterr().err
        assert "Opening map page 1." in err
        assert "Opening map page 1." in log_path.read_text(encoding="utf-8")
        with urlopen(viewer_url + "/api/status", timeout=2) as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body["state"] == "running"
        assert "latitude 19.0164" in body["summary"]
        request = Request(
            viewer_url + "/api/live",
            data=json.dumps(
                {
                    "lat": 19.0164,
                    "lon": 73.0373,
                    "radius_km": 0.9,
                    "keyword": "cafe",
                    "max_searches": 1,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=2) as response:
            again = json.loads(response.read().decode("utf-8"))
        assert again["state"] == "running"
        assert "already running" in again["message"]
        assert "restaurant" in again["summary"]
        with pytest.raises(HTTPError) as caught:
            urlopen(viewer_url + "/api/demo", timeout=2)
        assert caught.value.code == 409
        detail = json.loads(caught.value.read().decode("utf-8"))
        assert "practice map" in detail["error"]
    finally:
        release.set()
        wait_job(5)


def test_previous_viewer_gets_a_new_port() -> None:
    class OldPage(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b"<html><title>Coverage viewer</title><button>Run the demo</button></html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    old = ThreadingHTTPServer(("127.0.0.1", 0), OldPage)
    thread = threading.Thread(target=old.serve_forever, daemon=True)
    thread.start()
    busy = int(old.server_address[1])
    url, servers, note = plan_listen(busy)
    try:
        assert url is None
        assert servers
        assert int(servers[0].server_address[1]) != busy
        assert "previous viewer" in note
        assert "Run the demo" in note
    finally:
        old.shutdown()
        old.server_close()
        thread.join(timeout=2)
        for server in servers:
            server.server_close()


def test_viewer_refuses_a_public_host() -> None:
    with pytest.raises(SystemExit):
        main(["--host", "0.0.0.0", "--port", "8765"])


def test_running_viewer_is_reused(viewer_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    port = viewer_url.rsplit(":", 1)[-1]
    assert main(["--port", port]) == 0
    err = capsys.readouterr().err
    assert "already running" in err
    assert f"http://127.0.0.1:{port}/" in err


def test_taken_port_moves_to_the_next_free_one() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    busy = int(sock.getsockname()[1])
    try:
        url, servers, _note = plan_listen(busy)
    finally:
        sock.close()
    assert url is None
    try:
        assert servers
        assert int(servers[0].server_address[1]) != busy
    finally:
        for server in servers:
            server.server_close()


def _ipv6_loopback_works() -> bool:
    try:
        skipped = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        skipped.bind(("::1", 0))
    except OSError:
        return False
    skipped.close()
    return True


def test_new_viewer_answers_on_ipv4_and_ipv6() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    _url, servers, _note = plan_listen(port)
    threads: list[threading.Thread] = []
    try:
        for server in servers:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            threads.append(thread)
        bound = int(servers[0].server_address[1])
        with urlopen(f"http://127.0.0.1:{bound}/", timeout=2) as response:
            assert b"Coverage viewer" in response.read(2000)
        has_v6 = any(server.address_family == socket.AF_INET6 for server in servers)
        if _ipv6_loopback_works():
            assert has_v6
            with urlopen(f"http://[::1]:{bound}/", timeout=2) as response:
                assert response.status == 200
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)
