"""One gosom process per planner cell.

The planner chooses the cell. This module turns that cell into a single
Google Maps view: a keyword, a center, and a zoom. It never passes gosom's
grid flags or fast mode. Those modes either subdivide the area themselves or
stop around 21 rows.

Polygon clipping stays in the planner. This adapter only returns place ID,
longitude, latitude, and rank. Other scraper fields are discarded.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from shapely.geometry.base import BaseGeometry

from geocoverage.geometry import LocalProjection
from geocoverage.search import Place, ProviderError, SearchVariant

# Web-mercator meters per pixel at zoom 0 on the equator (2 * pi * 6378137 / 256).
_EQUATOR_MPP_ZOOM0 = 15_6543.03392804097

# Playwright's default window is 1280x720. The maps result list covers part of
# that width, so the fitter treats about 800px as the visible map pane.
MAP_VIEWPORT_PX = 800

# One Google Maps results list is about 120 places. Saturation uses this cap.
MAPS_VIEW_CAP = 120

# Depth 10 stopped at 75 rows on a zoom 14 and a zoom 15 view, under the
# split line. A depth 20 scroll on the same zoom 15 view reached 117 rows,
# which is the maps slice this planner is measuring. gosom also cancels a
# scroll that is still running at its 60s stats tick when exit-on-inactivity
# is set, because the first job has not completed yet. The process timeout is
# the guardrail instead.
MAX_DEPTH = 20

FORBIDDEN_FLAGS = frozenset(
    {
        "-fast-mode",
        "-grid-bbox",
        "-grid-cell",
        "-email",
        "-extra-reviews",
        "-debug",
        "-web",
        "-dsn",
    }
)

_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"']+")
_DURATION_RE = re.compile(r"^[1-9][0-9]*(ms|s|m|h)$")
_LANG_RE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")


@dataclass(frozen=True)
class MapView:
    """One map viewport. ``geo`` is gosom's latitude,longitude order."""

    lat: float
    lon: float
    zoom: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.lat) or not math.isfinite(self.lon):
            raise ValueError("map view coordinates must be finite")
        if self.zoom < 1 or self.zoom > 21:
            raise ValueError("zoom must be from 1 to 21")

    @property
    def geo(self) -> str:
        return f"{self.lat:.6f},{self.lon:.6f}"


class GosomFetcher(Protocol):
    def fetch(self, *, keyword: str, view: MapView) -> str:
        """Return one view's JSON text, or raise ``ProviderError``."""


def redact(text: str) -> str:
    """Remove proxy URLs from text that might be printed or stored."""
    return _URL_RE.sub("[redacted-url]", text)


def validate_keyword(keyword: str) -> str:
    text = keyword.strip()
    if not text:
        raise ValueError("keyword must be a non-empty search phrase")
    if any(char in text for char in "\n\r"):
        raise ValueError("keyword must be a single line")
    if "#!#" in text:
        raise ValueError("keyword must not contain a gosom custom-id marker")
    lowered = text.lower()
    if (
        "://" in lowered
        or lowered.startswith("maps.app.goo.gl")
        or "/maps" in lowered
    ):
        raise ValueError("keyword must be a search phrase, not a maps URL")
    return text


def validate_lang(lang: str) -> str:
    if not _LANG_RE.fullmatch(lang):
        raise ValueError("lang must look like 'en' or 'en-US'")
    return lang


def validate_depth(depth: int) -> int:
    if depth < 1 or depth > MAX_DEPTH:
        raise ValueError(f"depth must be from 1 to {MAX_DEPTH}")
    return depth


def validate_inactivity(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if not _DURATION_RE.fullmatch(value):
        raise ValueError("exit-on-inactivity must look like '3m'")
    return value


def meters_per_pixel(lat_deg: float, zoom: int) -> float:
    if zoom < 0 or zoom > 21:
        raise ValueError("zoom must be from 0 to 21")
    clamped = min(89.9, max(-89.9, lat_deg))
    return math.cos(math.radians(clamped)) * _EQUATOR_MPP_ZOOM0 / (2**zoom)


def ground_span_m(
    lat_deg: float,
    zoom: int,
    *,
    viewport_px: float = MAP_VIEWPORT_PX,
) -> float:
    if viewport_px <= 0:
        raise ValueError("viewport_px must be positive")
    return meters_per_pixel(lat_deg, zoom) * viewport_px


def zoom_for_span(
    lat_deg: float,
    span_m: float,
    *,
    viewport_px: float = MAP_VIEWPORT_PX,
) -> int:
    """Tightest zoom whose assumed map pane still covers ``span_m``."""
    if span_m <= 0:
        return 21
    for zoom in range(21, 0, -1):
        if ground_span_m(lat_deg, zoom, viewport_px=viewport_px) >= span_m:
            return zoom
    return 1


def view_for_geometry(
    geometry_m: BaseGeometry,
    projection: LocalProjection,
    *,
    zoom_delta: int = 0,
) -> MapView:
    if geometry_m.is_empty or geometry_m.area == 0:
        raise ValueError("cell geometry is empty")
    minx, miny, maxx, maxy = geometry_m.bounds
    span_m = max(maxx - minx, maxy - miny)
    # The map URL is a rectangle around a center. The bounds center frames the
    # cell; a centroid would drift on an L and can round to a different geo
    # string than the same cell's bounding rectangle.
    center_x = (minx + maxx) / 2.0
    center_y = (miny + maxy) / 2.0
    lon, lat = projection.inverse.transform(center_x, center_y)
    zoom = zoom_for_span(float(lat), span_m) + zoom_delta
    zoom = max(1, min(21, zoom))
    return MapView(lat=float(lat), lon=float(lon), zoom=zoom)


def assert_one_view(args: Sequence[str]) -> None:
    found = FORBIDDEN_FLAGS.intersection(args)
    if found:
        names = ", ".join(sorted(found))
        raise ValueError(f"one-view command contains forbidden flags: {names}")


def one_view_args(
    *,
    input_path: str,
    results_path: str,
    view: MapView,
    depth: int,
    lang: str,
    exit_on_inactivity: str | None,
    proxies_file: str | None,
) -> list[str]:
    """Arguments for one gosom process. Grid and fast mode are absent."""
    depth = validate_depth(depth)
    lang = validate_lang(lang)
    inactivity = validate_inactivity(exit_on_inactivity)
    args = [
        "-input",
        input_path,
        "-results",
        results_path,
        "-json",
        "-depth",
        str(depth),
        "-c",
        "1",
        "-lang",
        lang,
        "-geo",
        view.geo,
        "-zoom",
        str(view.zoom),
    ]
    if inactivity is not None:
        args.extend(["-exit-on-inactivity", inactivity])
    if proxies_file is not None:
        args.extend(["-proxies-file", proxies_file])
    assert_one_view(args)
    return args


def docker_run_args(
    *,
    image: str,
    container_name: str,
    work_host: Path,
    proxies_host: Path | None,
    scraper_args: list[str],
) -> list[str]:
    if not image or image.startswith("-"):
        raise ValueError("docker image name is missing")
    command = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--name",
        container_name,
        "--network",
        "bridge",
        "--shm-size=1gb",
        "-v",
        f"{work_host}:/work",
    ]
    if proxies_host is not None:
        command.extend(["-v", f"{proxies_host}:/proxies/proxies.txt:ro"])
    command.append(image)
    command.extend(scraper_args)
    assert_one_view(command)
    return command


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, 15)
    except OSError:
        process.kill()
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, 9)
        except OSError:
            process.kill()
        process.wait(timeout=5)


def docker_kill(name: str) -> str:
    """Stop a container. Return a redacted note when the kill command fails."""
    try:
        subprocess.run(
            ["docker", "kill", name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return redact(str(exc))
    return ""


def run_command(
    command: Sequence[str],
    *,
    timeout_s: float,
    on_timeout: Callable[[], str] | None = None,
) -> None:
    """Run ``command`` without a shell. Raise ``ProviderError`` on failure."""
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    if not command:
        raise ValueError("command is empty")
    try:
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ProviderError(f"could not start {command[0]}") from exc
    try:
        _stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        _kill_process_group(process)
        note = on_timeout() if on_timeout is not None else ""
        message = f"gosom view timed out after {timeout_s:.0f}s"
        if note:
            message = f"{message}; {note}"
        raise ProviderError(message) from exc
    if process.returncode != 0:
        tail = redact(stderr or "")[-500:]
        raise ProviderError(f"gosom exited {process.returncode}: {tail}")


def _as_finite_float(value: object, *, label: str, index: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProviderError(f"gosom row {index} has a malformed {label}")
    number = float(value)
    if not math.isfinite(number):
        raise ProviderError(f"gosom row {index} has a malformed {label}")
    return number


def _coordinate(
    row: Mapping[str, object],
    keys: tuple[str, ...],
    *,
    label: str,
    index: int,
) -> float:
    present = [key for key in keys if key in row and row[key] is not None]
    if not present:
        raise ProviderError(f"gosom row {index} is missing {label}")
    values = [_as_finite_float(row[key], label=label, index=index) for key in present]
    if max(values) - min(values) > 1e-9:
        raise ProviderError(f"gosom row {index} has conflicting {label} fields")
    return values[0]


def _place_from_row(row: object, index: int) -> Place:
    if not isinstance(row, dict):
        raise ProviderError(f"gosom row {index} is not an object")
    place_id = row.get("place_id")
    if not isinstance(place_id, str) or not place_id.strip():
        raise ProviderError(f"gosom row {index} is missing place_id")
    lat = _coordinate(row, ("latitude",), label="latitude", index=index)
    lon = _coordinate(
        row,
        ("longitude", "longtitude"),
        label="longitude",
        index=index,
    )
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ProviderError(f"gosom row {index} has coordinates outside WGS84")
    title = row.get("title")
    label = title.strip() if isinstance(title, str) else ""
    return Place(place_id=place_id.strip(), lon=lon, lat=lat, rank=index, label=label)


def parse_gosom_places(text: str) -> list[Place]:
    """Parse a JSON array or JSONL page into ranked places.

    Malformed rows raise ``ProviderError``. Names and contact fields are ignored.
    """
    if not text.strip():
        return []
    rows: list[object]
    if text.lstrip().startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"gosom JSON is malformed: {exc.msg}") from exc
        if not isinstance(parsed, list):
            raise ProviderError("gosom JSON is malformed")
        rows = parsed
    else:
        rows = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ProviderError(
                    f"gosom JSON line {line_no} is malformed: {exc.msg}"
                ) from exc
    places: list[Place] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        place = _place_from_row(row, index)
        if place.place_id in seen:
            continue
        seen.add(place.place_id)
        places.append(place)
    return places


@dataclass(frozen=True)
class FetchRecord:
    lat: float
    lon: float
    zoom: int
    cached: bool
    seconds: float
    rows: int


class GosomSearch:
    """Search adapter. One fetch is one cell, centered and zoomed to that cell.

    An identical view inside one run reuses the captured page. The comparison
    searches the root rectangle once on its own and the planner searches it
    again; repeating that browser session would not be a new view.
    """

    def __init__(
        self,
        *,
        keyword: str,
        projection: LocalProjection,
        fetcher: GosomFetcher,
        probe_zoom_delta: int = 1,
        on_event: Callable[[str], None] | None = None,
    ) -> None:
        if probe_zoom_delta < 0 or probe_zoom_delta > 5:
            raise ValueError("probe_zoom_delta must be from 0 to 5")
        self.keyword = validate_keyword(keyword)
        self.projection = projection
        self.cap = MAPS_VIEW_CAP
        self.fetcher = fetcher
        self._probe_zoom_delta = probe_zoom_delta
        self._on_event = on_event or (lambda _text: None)
        self._cache: dict[tuple[str, str, int], list[Place]] = {}
        self.fetches: list[FetchRecord] = []

    def search(
        self,
        geometry_m: BaseGeometry,
        *,
        variant: SearchVariant = "primary",
    ) -> list[Place]:
        delta = self._probe_zoom_delta if variant == "probe" else 0
        view = view_for_geometry(geometry_m, self.projection, zoom_delta=delta)
        key = (self.keyword, view.geo, view.zoom)
        cached = self._cache.get(key)
        if cached is not None:
            self.fetches.append(
                FetchRecord(
                    lat=view.lat,
                    lon=view.lon,
                    zoom=view.zoom,
                    cached=True,
                    seconds=0.0,
                    rows=len(cached),
                )
            )
            self._on_event(
                f"Reused cached view lat={view.lat:.6f} lon={view.lon:.6f} "
                f"zoom={view.zoom} ({len(cached)} rows)."
            )
            return list(cached)
        self._on_event(
            f"Fetching view lat={view.lat:.6f} lon={view.lon:.6f} zoom={view.zoom}."
        )
        started = time.monotonic()
        try:
            payload = self.fetcher.fetch(keyword=self.keyword, view=view)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("gosom view failed") from exc
        self._on_event(f"Parsing {len(payload)} bytes of results.")
        places = parse_gosom_places(payload)
        elapsed = time.monotonic() - started
        self._cache[key] = places
        self.fetches.append(
            FetchRecord(
                lat=view.lat,
                lon=view.lon,
                zoom=view.zoom,
                cached=False,
                seconds=elapsed,
                rows=len(places),
            )
        )
        self._on_event(f"Fetched {len(places)} rows in {elapsed:.0f}s.")
        return list(places)


class WorkspaceRunner:
    """Start one gosom CLI or container and read its results file."""

    def __init__(
        self,
        *,
        mode: str,
        binary: Sequence[str] = (),
        image: str = "gosom/google-maps-scraper:latest",
        depth: int = MAX_DEPTH,
        lang: str = "en",
        timeout_s: float = 1200,
        exit_on_inactivity: str | None = None,
        proxies_file: Path | None = None,
        fetch_budget: int | None = None,
        on_event: Callable[[str], None] | None = None,
    ) -> None:
        if mode not in {"binary", "docker"}:
            raise ValueError("mode must be 'binary' or 'docker'")
        if mode == "binary" and not binary:
            raise ValueError("binary mode needs the gosom executable")
        if fetch_budget is not None and fetch_budget < 1:
            raise ValueError("fetch_budget must be at least 1")
        self.mode = mode
        self.binary = tuple(binary)
        self.image = image
        self.depth = validate_depth(depth)
        self.lang = validate_lang(lang)
        self.timeout_s = timeout_s
        self.exit_on_inactivity = validate_inactivity(exit_on_inactivity)
        self.proxies_file = proxies_file
        self.fetch_budget = fetch_budget
        self._on_event = on_event or (lambda _text: None)
        self.started = 0
        if proxies_file is not None and not proxies_file.is_file():
            raise ValueError("proxies file is missing")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")

    def fetch(self, *, keyword: str, view: MapView) -> str:
        if self.fetch_budget is not None and self.started >= self.fetch_budget:
            raise ProviderError("gosom fetch budget exhausted")
        self.started += 1
        keyword = validate_keyword(keyword)
        with tempfile.TemporaryDirectory(prefix="geocoverage-gosom-") as tmp:
            work = Path(tmp)
            query_path = work / "query.txt"
            results_path = work / "results.json"
            query_path.write_text(keyword + "\n", encoding="utf-8")
            on_timeout: Callable[[], str] | None = None
            if self.mode == "binary":
                scraper_args = one_view_args(
                    input_path=str(query_path),
                    results_path=str(results_path),
                    view=view,
                    depth=self.depth,
                    lang=self.lang,
                    exit_on_inactivity=self.exit_on_inactivity,
                    proxies_file=str(self.proxies_file) if self.proxies_file else None,
                )
                command: list[str] = [*self.binary, *scraper_args]
                how = "binary"
            else:
                container_name = f"geocoverage-gosom-{uuid.uuid4().hex[:12]}"
                proxies_in_container = (
                    "/proxies/proxies.txt" if self.proxies_file is not None else None
                )
                scraper_args = one_view_args(
                    input_path="/work/query.txt",
                    results_path="/work/results.json",
                    view=view,
                    depth=self.depth,
                    lang=self.lang,
                    exit_on_inactivity=self.exit_on_inactivity,
                    proxies_file=proxies_in_container,
                )
                command = docker_run_args(
                    image=self.image,
                    container_name=container_name,
                    work_host=work,
                    proxies_host=self.proxies_file,
                    scraper_args=scraper_args,
                )
                how = "docker"

                def on_timeout(name: str = container_name) -> str:
                    return docker_kill(name)

            self._on_event(
                f"Starting {how} browser for lat={view.lat:.6f} lon={view.lon:.6f} "
                f"zoom={view.zoom} depth={self.depth}."
            )
            started = time.monotonic()
            run_command(command, timeout_s=self.timeout_s, on_timeout=on_timeout)
            browser_s = time.monotonic() - started
            self._on_event(f"Browser finished in {browser_s:.0f}s.")
            if not results_path.is_file():
                raise ProviderError("gosom exited without a results file")
            payload = results_path.read_text(encoding="utf-8")
            self._on_event(f"Results file is {len(payload)} bytes.")
            return payload
