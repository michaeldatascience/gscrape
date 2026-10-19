import json
import socket
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from shapely.geometry import box

from geocoverage.planner import CellTrace
from geocoverage.search import Place
from geocoverage.session import SessionView, run_demo, view_payload
from geocoverage.ui import PAGE_HTML, Handler, main, plan_listen


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
    assert "Run the demo" in PAGE_HTML
    assert "Play the split" in PAGE_HTML
    assert "Search Google Maps" in PAGE_HTML
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
    assert b"Run the demo" in page.read()
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
        url, servers = plan_listen(busy)
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
    _url, servers = plan_listen(port)
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
