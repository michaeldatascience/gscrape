"""Localhost page for watching a coverage run.

The demo button uses the fake 500-place square. Search Google Maps is the live
path and needs Docker. This page is the experiment viewer, not the product
application.
"""

from __future__ import annotations

import argparse
import errno
import json
import math
import socket
import sys
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from geocoverage.geometry import circle_region
from geocoverage.gosom import redact, validate_keyword
from geocoverage.session import SessionView, run_demo, run_live, view_payload

_RUN_LOCK = threading.Lock()
_LIVE_IMAGE = "gosom/google-maps-scraper:latest"


PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Coverage viewer</title>
<style>
:root {
  --ink: #1c1915;
  --paper: #f3ecdf;
  --card: #fffaf2;
  --line: #e2d8c8;
  --muted: #5e574e;
  --orange: #c45c26;
  --green: #2f6f4e;
  --grey: #8a8f98;
  --gold: #8a5a00;
  --red: #8b2e2e;
  --purple: #6b3fa0;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  color: var(--ink);
  background: var(--paper);
  font: 16px/1.45 Palatino, "Palatino Linotype", Georgia, serif;
}
header, main { max-width: 1120px; margin: 0 auto; padding: 1.2rem 1.2rem 0; }
h1 { font-size: 1.9rem; font-weight: 600; margin: 0 0 0.3rem; }
h2 { font-size: 1.15rem; margin: 0 0 0.4rem; }
.lede, .note { color: var(--muted); margin: 0.2rem 0 0.8rem; }
form, .panel, .stat {
  background: var(--card);
  border: 1px solid var(--line);
}
form {
  display: flex;
  flex-wrap: wrap;
  gap: 0.65rem 0.8rem;
  align-items: end;
  padding: 0.8rem;
  margin: 0 0 0.8rem;
}
label { display: flex; flex-direction: column; gap: 0.2rem; font-size: 0.85rem; }
input[type="number"], input[type="text"] {
  font: 16px/1.2 ui-monospace, monospace;
  padding: 0.35rem 0.45rem;
  border: 1px solid #c9bfb0;
  background: white;
  color: var(--ink);
  width: 8.5rem;
}
#keyword { width: 11rem; }
button {
  font: 16px/1.2 Palatino, Georgia, serif;
  background: var(--ink);
  color: var(--paper);
  border: 0;
  padding: 0.5rem 0.75rem;
  cursor: pointer;
}
button.secondary { background: transparent; color: var(--ink); border: 1px solid var(--ink); }
button:disabled { opacity: 0.55; cursor: wait; }
.warn { flex-basis: 100%; margin: 0; color: #6b3a1f; }
#banner { min-height: 1.4rem; margin: 0 0 0.6rem; font-weight: 600; }
.stats { display: flex; flex-wrap: wrap; gap: 0.6rem; margin-bottom: 0.8rem; }
.stat { padding: 0.45rem 0.7rem; min-width: 9rem; }
.stat b { display: block; font-size: 1.35rem; font-variant-numeric: tabular-nums; }
#stage { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(280px, 0.8fr); gap: 0.8rem; }
@media (max-width: 860px) { #stage { grid-template-columns: 1fr; } }
#map { background: #f7f1e6; border: 1px solid var(--line); }
#map svg { display: block; width: 100%; height: auto; max-height: 560px; }
.legend { display: flex; flex-wrap: wrap; gap: 0.4rem 0.9rem; list-style: none; padding: 0.4rem 0 0; margin: 0; font-size: 0.9rem; }
.legend i { display: inline-block; width: 0.75rem; height: 0.75rem; margin-right: 0.25rem; vertical-align: -1px; }
.split { background: var(--orange); }
.stop { background: var(--green); }
.budget { background: var(--grey); }
.too_small { background: var(--gold); }
.fetch_failed { background: var(--red); }
.ranking_risk { background: var(--purple); }
aside.panel { padding: 0.8rem; max-height: 640px; overflow: auto; }
#depth { width: 100%; }
#tree { margin-top: 0.8rem; max-height: 280px; overflow: auto; border-top: 1px solid var(--line); }
#tree button {
  display: block;
  width: 100%;
  text-align: left;
  background: transparent;
  color: var(--ink);
  border: 0;
  border-left: 4px solid transparent;
  border-radius: 0;
  padding: 0.28rem 0.4rem;
  font: 14px/1.35 ui-monospace, monospace;
}
#tree button[aria-current="true"] { background: #f3e6d4; }
#places { max-height: 280px; overflow: auto; padding-left: 1.15rem; margin: 0.4rem 0 0; }
#places li { margin: 0.15rem 0; }
code { font-family: ui-monospace, monospace; font-size: 0.82em; }
#claim { margin-top: 0.8rem; }
footer { max-width: 1120px; margin: 0 auto; padding: 0.8rem 1.2rem 2rem; color: var(--muted); }
</style>
</head>
<body>
<header>
  <h1>Coverage viewer</h1>
  <p class="lede">One map view returns about 120 places. Quarter a piece when the page is full, and stop when the next page comes back short.</p>
</header>
<main>
  <form id="demo-form">
    <button type="button" id="demo">Run the demo</button>
    <button type="button" id="demo-budget" class="secondary">Stop after 1 search</button>
    <label>Max searches
      <input id="demo-limit" type="number" min="1" max="200" value="30"/>
    </label>
  </form>
  <form id="live-form">
    <label>Latitude
      <input id="lat" type="text" value="51.514" autocomplete="off"/>
    </label>
    <label>Longitude
      <input id="lon" type="text" value="-0.1325" autocomplete="off"/>
    </label>
    <label>Radius km
      <input id="radius" type="text" value="0.9" autocomplete="off"/>
    </label>
    <label>Keyword
      <input id="keyword" type="text" value="restaurant" autocomplete="off"/>
    </label>
    <label>Max searches
      <input id="live-limit" type="number" min="1" max="12" value="5"/>
    </label>
    <button type="submit" id="search">Search Google Maps</button>
    <p class="warn">Search Google Maps calls Google through a local scraper container. Each full page can take a few minutes. The demo button does not. The total number of places stays unknown.</p>
  </form>
  <p id="banner" role="status"></p>
  <section class="stats" id="stats" hidden>
    <div class="stat">One search<b id="one-search-count"></b></div>
    <div class="stat">After splitting<b id="planner-count"></b></div>
    <div class="stat">Only after the split<b id="only-split-count"></b></div>
    <div class="stat">Status<b id="run-status"></b></div>
  </section>
  <p id="rule" class="note"></p>
  <ul class="legend">
    <li><i class="split"></i>Quartered</li>
    <li><i class="stop"></i>Stopped, page was short</li>
    <li><i class="budget"></i>Not searched, limit reached</li>
    <li><i class="too_small"></i>Too small to split</li>
    <li><i class="fetch_failed"></i>Fetch failed</li>
    <li><i class="ranking_risk"></i>Ranking risk</li>
  </ul>
  <div id="stage">
    <div>
      <div id="map"></div>
      <p class="note">Dots are places kept inside the area for the smallest pieces currently shown. North is up. Drag the depth back to see only the first page.</p>
    </div>
    <aside class="panel">
      <label>Split depth
        <input id="depth" type="range" min="0" max="0" value="0"/>
      </label>
      <p id="depth-label" class="note"></p>
      <button type="button" id="play" class="secondary">Play the split</button>
      <div id="tree"></div>
      <article id="detail"></article>
    </aside>
  </div>
  <p id="claim"></p>
</main>
<footer>Local viewer for this experiment. It does not report a completeness percentage.</footer>
<script>
const SVG = "http://www.w3.org/2000/svg";
const COLORS = {
  split: "#c45c26",
  stop: "#2f6f4e",
  budget: "#8a8f98",
  too_small: "#8a5a00",
  fetch_failed: "#8b2e2e",
  ranking_risk: "#6b3fa0"
};
let report = null;
let selectedPath = "0";
let cutoff = 0;
let timer = null;
let busy = false;

function setDisabled(value) {
  ["demo", "demo-budget", "search", "play"].forEach((id) => {
    document.getElementById(id).disabled = value;
  });
}

function stopPlay() {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
  document.getElementById("play").textContent = "Play the split";
}

function visibleCells() {
  return report.cells.filter((cell) => cell.depth <= cutoff);
}

function visibleSelection() {
  const visible = visibleCells();
  let path = selectedPath;
  while (path && !visible.some((cell) => cell.path === path)) {
    path = path.length > 1 ? path.slice(0, -1) : "";
  }
  return visible.find((cell) => cell.path === path) || visible[0] || null;
}

function show(data) {
  stopPlay();
  report = data;
  selectedPath = "0";
  const maxDepth = data.cells.reduce((max, cell) => Math.max(max, cell.depth), 0);
  const slider = document.getElementById("depth");
  slider.max = String(maxDepth);
  slider.value = String(maxDepth);
  cutoff = maxDepth;
  document.getElementById("stats").hidden = false;
  document.getElementById("one-search-count").textContent = String(data.single_search_in_polygon);
  document.getElementById("planner-count").textContent = String(data.planner_in_polygon);
  document.getElementById("only-split-count").textContent = String(data.only_planner);
  document.getElementById("run-status").textContent = data.status;
  document.getElementById("claim").textContent = data.claim;
  document.getElementById("rule").textContent =
    "A page is quartered when it returns at least " + data.split_at +
    " of " + data.cap + " rows. A shorter page stops. The search limit here is " +
    data.max_searches + ".";
  render();
}

function render() {
  if (!report) return;
  drawMap();
  drawTree();
  drawDetail();
  document.getElementById("depth-label").textContent =
    "Showing depth 0 through " + cutoff + ". The counts above are for the whole run.";
}

function bounds() {
  let minx = Infinity;
  let miny = Infinity;
  let maxx = -Infinity;
  let maxy = -Infinity;
  function consider(lon, lat) {
    minx = Math.min(minx, lon);
    miny = Math.min(miny, lat);
    maxx = Math.max(maxx, lon);
    maxy = Math.max(maxy, lat);
  }
  report.rings.forEach((ring) => ring.forEach((pair) => consider(pair[0], pair[1])));
  report.cells.forEach((cell) => {
    consider(cell.west, cell.south);
    consider(cell.east, cell.north);
  });
  const spanX = Math.max(maxx - minx, 1e-9);
  const spanY = Math.max(maxy - miny, 1e-9);
  return {
    minx: minx - spanX * 0.08,
    miny: miny - spanY * 0.08,
    maxx: maxx + spanX * 0.08,
    maxy: maxy + spanY * 0.08
  };
}

function drawMap() {
  const host = document.getElementById("map");
  host.replaceChildren();
  const box = bounds();
  const width = 760;
  const spanX = box.maxx - box.minx;
  const spanY = box.maxy - box.miny;
  const height = Math.max(320, Math.round(width * spanY / spanX));
  function xy(lon, lat) {
    return [(lon - box.minx) / spanX * width, (box.maxy - lat) / spanY * height];
  }
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 " + width + " " + height);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Searched pieces");
  const defs = document.createElementNS(SVG, "defs");
  const clip = document.createElementNS(SVG, "clipPath");
  clip.setAttribute("id", "area-clip");
  report.rings.forEach((ring) => {
    const poly = document.createElementNS(SVG, "polygon");
    poly.setAttribute("points", ring.map((pair) => {
      const point = xy(pair[0], pair[1]);
      return point[0].toFixed(1) + "," + point[1].toFixed(1);
    }).join(" "));
    clip.appendChild(poly);
  });
  defs.appendChild(clip);
  svg.appendChild(defs);
  const layer = document.createElementNS(SVG, "g");
  if (report.rings.length) layer.setAttribute("clip-path", "url(#area-clip)");
  const visible = visibleCells();
  const leaves = new Set(visible.map((cell) => cell.path));
  visible.forEach((cell) => {
    if (cell.depth === 0) return;
    const parent = cell.path.slice(0, -1);
    leaves.delete(parent);
  });
  const selected = visibleSelection();
  const labels = [];
  visible.slice().sort((a, b) => a.depth - b.depth).forEach((cell) => {
    const nw = xy(cell.west, cell.north);
    const se = xy(cell.east, cell.south);
    const rect = document.createElementNS(SVG, "rect");
    const color = COLORS[cell.decision] || "#333";
    rect.setAttribute("x", Math.min(nw[0], se[0]).toFixed(1));
    rect.setAttribute("y", Math.min(nw[1], se[1]).toFixed(1));
    rect.setAttribute("width", Math.abs(se[0] - nw[0]).toFixed(1));
    rect.setAttribute("height", Math.abs(se[1] - nw[1]).toFixed(1));
    const isLeaf = leaves.has(cell.path);
    rect.setAttribute("fill", color);
    rect.setAttribute("fill-opacity", isLeaf ? "0.62" : "0.08");
    rect.setAttribute("stroke", selected && selected.path === cell.path ? "#1c1915" : color);
    rect.setAttribute("stroke-width", selected && selected.path === cell.path ? "3" : "1.5");
    const title = document.createElementNS(SVG, "title");
    title.textContent = cell.name + " " + cell.decision;
    rect.appendChild(title);
    layer.appendChild(rect);
    if (cell.depth === 1) {
      const text = document.createElementNS(SVG, "text");
      text.setAttribute("x", ((nw[0] + se[0]) / 2).toFixed(1));
      text.setAttribute("y", ((nw[1] + se[1]) / 2).toFixed(1));
      text.setAttribute("text-anchor", "middle");
      text.setAttribute("dominant-baseline", "middle");
      text.setAttribute("fill", "#1c1915");
      text.setAttribute("stroke", "#f7f1e6");
      text.setAttribute("stroke-width", "5");
      text.setAttribute("paint-order", "stroke");
      text.setAttribute("font-size", "18");
      text.setAttribute("font-family", "Palatino, Georgia, serif");
      text.textContent = cell.name;
      labels.push(text);
    }
  });
  visible.filter((cell) => leaves.has(cell.path)).forEach((cell) => {
    cell.places.forEach((place) => {
      const point = xy(place.lon, place.lat);
      const dot = document.createElementNS(SVG, "circle");
      dot.setAttribute("cx", point[0].toFixed(1));
      dot.setAttribute("cy", point[1].toFixed(1));
      dot.setAttribute("r", "2.4");
      dot.setAttribute("fill", "#1c1915");
      dot.setAttribute("fill-opacity", "0.75");
      layer.appendChild(dot);
    });
  });
  labels.forEach((text) => layer.appendChild(text));
  svg.appendChild(layer);
  report.rings.forEach((ring) => {
    const poly = document.createElementNS(SVG, "polygon");
    poly.setAttribute("points", ring.map((pair) => {
      const point = xy(pair[0], pair[1]);
      return point[0].toFixed(1) + "," + point[1].toFixed(1);
    }).join(" "));
    poly.setAttribute("fill", "none");
    poly.setAttribute("stroke", "#1c1915");
    poly.setAttribute("stroke-width", "2");
    svg.appendChild(poly);
  });
  svg.addEventListener("click", (event) => {
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const matrix = svg.getScreenCTM();
    if (!matrix) return;
    const local = point.matrixTransform(matrix.inverse());
    const lon = box.minx + (local.x / width) * spanX;
    const lat = box.maxy - (local.y / height) * spanY;
    let best = null;
    visible.forEach((cell) => {
      if (lon < cell.west || lon > cell.east || lat < cell.south || lat > cell.north) return;
      if (!best || cell.depth >= best.depth) best = cell;
    });
    if (!best) return;
    selectedPath = best.path;
    render();
  });
  host.appendChild(svg);
}

function drawTree() {
  const tree = document.getElementById("tree");
  tree.replaceChildren();
  visibleCells().forEach((cell) => {
    const button = document.createElement("button");
    button.type = "button";
    button.style.paddingLeft = (8 + cell.depth * 14) + "px";
    button.style.borderLeftColor = COLORS[cell.decision] || "#333";
    button.textContent = cell.name + " · " + cell.decision + " · " + cell.rows + " rows";
    if (visibleSelection() && visibleSelection().path === cell.path) {
      button.setAttribute("aria-current", "true");
    }
    button.addEventListener("click", () => {
      selectedPath = cell.path;
      render();
    });
    tree.appendChild(button);
  });
}

function drawDetail() {
  const detail = document.getElementById("detail");
  detail.replaceChildren();
  const cell = visibleSelection();
  if (!cell) return;
  const heading = document.createElement("h2");
  heading.id = "piece-name";
  heading.textContent = cell.name;
  const decision = document.createElement("p");
  decision.textContent = cell.decision;
  const body = document.createElement("p");
  body.textContent = cell.detail;
  const counts = document.createElement("p");
  counts.textContent = "Rows " + cell.rows + ". Inside " + cell.in_polygon +
    ". Outside " + cell.outside + ". New IDs " + cell.new_ids + ".";
  detail.append(heading, decision, body, counts);
  if (!cell.places.length) {
    const empty = document.createElement("p");
    empty.textContent = "No in-area places on this page.";
    detail.appendChild(empty);
    return;
  }
  const list = document.createElement("ol");
  list.id = "places";
  cell.places.forEach((place) => {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = place.label || place.place_id;
    item.appendChild(name);
    if (place.label && place.label !== place.place_id) {
      const code = document.createElement("code");
      code.textContent = " " + place.place_id;
      item.appendChild(code);
    }
    const where = document.createElement("span");
    where.textContent = "  " + Number(place.lat).toFixed(5) + ", " + Number(place.lon).toFixed(5);
    item.appendChild(where);
    if (report.mode === "live" && Number.isFinite(Number(place.lat)) && Number.isFinite(Number(place.lon))) {
      const link = document.createElement("a");
      link.href = "https://www.google.com/maps/search/?api=1&query=" +
        Number(place.lat).toFixed(6) + "," + Number(place.lon).toFixed(6) +
        "&query_place_id=" + encodeURIComponent(place.place_id);
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = " map";
      item.appendChild(link);
    }
    list.appendChild(item);
  });
  detail.appendChild(list);
}

async function readJson(response) {
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The request failed");
  return data;
}

async function loadDemo() {
  if (busy) return;
  stopPlay();
  busy = true;
  setDisabled(true);
  document.getElementById("banner").textContent = "Running the demo…";
  try {
    const limit = document.getElementById("demo-limit").value;
    const response = await fetch("/api/demo?max_searches=" + encodeURIComponent(limit));
    show(await readJson(response));
    document.getElementById("banner").textContent = "";
  } catch (error) {
    document.getElementById("banner").textContent = error.message || "Demo failed";
  } finally {
    busy = false;
    setDisabled(false);
  }
}

async function loadLive(event) {
  event.preventDefault();
  if (busy) return;
  stopPlay();
  busy = true;
  setDisabled(true);
  document.getElementById("banner").textContent =
    "Calling Google Maps. Each full page can take a few minutes. Leave this tab open.";
  try {
    const response = await fetch("/api/live", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lat: Number(document.getElementById("lat").value),
        lon: Number(document.getElementById("lon").value),
        radius_km: Number(document.getElementById("radius").value),
        keyword: document.getElementById("keyword").value,
        max_searches: Number(document.getElementById("live-limit").value)
      })
    });
    show(await readJson(response));
    document.getElementById("banner").textContent = "";
  } catch (error) {
    document.getElementById("banner").textContent = error.message || "Search failed";
  } finally {
    busy = false;
    setDisabled(false);
  }
}

document.getElementById("demo").addEventListener("click", loadDemo);
document.getElementById("demo-budget").addEventListener("click", () => {
  document.getElementById("demo-limit").value = "1";
  loadDemo();
});
document.getElementById("live-form").addEventListener("submit", loadLive);
document.getElementById("depth").addEventListener("input", () => {
  stopPlay();
  cutoff = Number(document.getElementById("depth").value);
  render();
});
document.getElementById("play").addEventListener("click", () => {
  if (!report || busy) return;
  if (timer) {
    stopPlay();
    return;
  }
  const slider = document.getElementById("depth");
  const max = Number(slider.max);
  cutoff = 0;
  slider.value = "0";
  render();
  document.getElementById("play").textContent = "Pause";
  timer = setInterval(() => {
    if (cutoff >= max) {
      stopPlay();
      return;
    }
    cutoff += 1;
    slider.value = String(cutoff);
    render();
    if (cutoff >= max) stopPlay();
  }, 900);
});
loadDemo();
</script>
</body>
</html>
"""


def _query_int(params: dict[str, list[str]], key: str, default: int, *, low: int, high: int) -> int:
    raw = params.get(key, [""])[0]
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value < low or value > high:
        raise ValueError(f"{key} must be from {low} to {high}")
    return value


def _query_float(
    params: dict[str, list[str]],
    key: str,
    default: float,
    *,
    low: float,
    high: float,
) -> float:
    raw = params.get(key, [""])[0]
    if raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number") from exc
    if not math.isfinite(value) or value < low or value > high:
        raise ValueError(f"{key} must be from {low} to {high}")
    return value


def demo_view(query: str) -> SessionView:
    params = parse_qs(query, keep_blank_values=True)
    limit = _query_int(params, "max_searches", 30, low=1, high=200)
    cell_m = _query_float(params, "min_cell_m", 200.0, low=50.0, high=5000.0)
    return run_demo(max_searches=limit, min_cell_m=cell_m)


def _required_float(data: dict[str, object], key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{key} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{key} must be a number")
    return number


def _bounded_int(data: dict[str, object], key: str, default: int, *, low: int, high: int) -> int:
    if key not in data or data[key] is None:
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    if value < low or value > high:
        raise ValueError(f"{key} must be from {low} to {high}")
    return value


def live_view(raw: bytes) -> SessionView:
    if len(raw) > 65_536:
        raise ValueError("request is too large")
    try:
        parsed: object = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("request body must be JSON") from exc
    if not isinstance(parsed, dict):
        raise TypeError("request body must be a JSON object")
    data: dict[str, object] = {}
    for key, value in parsed.items():
        if not isinstance(key, str):
            raise TypeError("request body must be a JSON object")
        data[key] = value
    lat = _required_float(data, "lat")
    lon = _required_float(data, "lon")
    radius_km = _required_float(data, "radius_km")
    keyword = data.get("keyword")
    if not isinstance(keyword, str):
        raise TypeError("keyword must be a search phrase")
    keyword = validate_keyword(keyword)
    limit = _bounded_int(data, "max_searches", 5, low=1, high=12)
    cell_m = 200.0
    if "min_cell_m" in data and data["min_cell_m"] is not None:
        cell_m = _required_float(data, "min_cell_m")
        if cell_m < 50 or cell_m > 5000:
            raise ValueError("min_cell_m must be from 50 to 5000")
    region = circle_region(lat, lon, radius_km)
    area = f"circle of {radius_km} km around latitude {lat}, longitude {lon}"
    return run_live(
        region,
        keyword=keyword,
        max_searches=limit,
        min_cell_m=cell_m,
        depth=20,
        timeout_s=1200.0,
        image=_LIVE_IMAGE,
        binary="",
        proxies_file=None,
        lang="en",
        exit_on_inactivity="",
        area_label=area,
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "geocoverage-ui/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE_HTML.encode("utf-8"))
            return
        if parsed.path == "/api/demo":
            self._run(lambda: demo_view(parsed.query))
            return
        self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/live":
            self._json(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self._json(400, {"error": "Content-Length must be an integer"})
            return
        if length < 0 or length > 65_536:
            self._json(400, {"error": "request is too large"})
            return
        raw = self.rfile.read(length)
        self._run(lambda: live_view(raw))

    def _run(self, build: Callable[[], SessionView]) -> None:
        if not _RUN_LOCK.acquire(blocking=False):
            self._json(409, {"error": "A search is already running."})
            return
        try:
            payload = view_payload(build())
        except (ValueError, TypeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        finally:
            _RUN_LOCK.release()
        self._json(200, payload)

    def _json(self, status: int, payload: dict[str, object]) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(redact(fmt % args) + "\n")


class IPv6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def bind_host(host: str) -> str:
    if host == "localhost":
        return "127.0.0.1"
    if host != "127.0.0.1":
        raise ValueError("the viewer binds to localhost only")
    return host


def probe_viewer(port: int) -> str | None:
    """Return the URL if this port is already serving the coverage page."""
    for url in (f"http://127.0.0.1:{port}/", f"http://[::1]:{port}/"):
        try:
            with urlopen(url, timeout=0.4) as response:
                if response.status != 200:
                    continue
                chunk = response.read(4096)
        except (OSError, URLError, TimeoutError):
            continue
        if b"Coverage viewer" in chunk:
            return url
    return None


def open_servers(port: int) -> list[ThreadingHTTPServer]:
    """Listen on the IPv4 loopback and, when the machine has it, IPv6 loopback."""
    if port == 0:
        return [ThreadingHTTPServer(("127.0.0.1", 0), Handler)]
    v4 = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    servers: list[ThreadingHTTPServer] = [v4]
    try:
        servers.append(IPv6Server(("::1", port), Handler))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            v4.server_close()
            raise
    return servers


def plan_listen(start: int, *, attempts: int = 20) -> tuple[str | None, list[ThreadingHTTPServer]]:
    """Reuse a viewer that is already up, or bind the next free loopback port."""
    if start == 0:
        return None, open_servers(0)
    last_error: OSError | None = None
    for offset in range(attempts):
        port = start + offset
        if port > 65535:
            break
        found = probe_viewer(port)
        if found is not None:
            return found, []
        try:
            return None, open_servers(port)
        except OSError as exc:
            last_error = exc
            if exc.errno != errno.EADDRINUSE:
                raise
    message = "address already in use" if last_error is None else last_error.strerror
    raise OSError(errno.EADDRINUSE, message)


def _close_servers(servers: list[ThreadingHTTPServer]) -> None:
    for server in servers:
        server.server_close()


def _serve(servers: list[ThreadingHTTPServer]) -> None:
    threads: list[threading.Thread] = []
    for extra in servers[1:]:
        thread = threading.Thread(target=extra.serve_forever, daemon=True)
        thread.start()
        threads.append(thread)
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for extra in servers[1:]:
            extra.shutdown()
        for server in servers:
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)


def _announce(requested: int, url: str | None, servers: list[ThreadingHTTPServer]) -> None:
    if url is not None:
        print(f"The viewer is already running.\nOpen {url}", file=sys.stderr)
        print(
            "Open that 127.0.0.1 address. A localhost tab can miss a server that is only on IPv4.",
            file=sys.stderr,
        )
        return
    port = int(servers[0].server_address[1])
    print(f"Open http://127.0.0.1:{port}/", file=sys.stderr)
    if requested not in {0, port}:
        print(f"Port {requested} was already in use, so this one is {port}.", file=sys.stderr)
    if any(server.address_family == socket.AF_INET6 for server in servers):
        print(f"localhost also works: http://localhost:{port}/", file=sys.stderr)
    print("The demo button does not call Google. Search Google Maps does.", file=sys.stderr)


def serve(host: str, port: int) -> None:
    if host != "127.0.0.1":
        raise ValueError("the viewer binds to localhost only")
    url, servers = plan_listen(port)
    if url is not None:
        _announce(port, url, servers)
        return
    _announce(port, None, servers)
    _serve(servers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Open a localhost page that draws how a search area is split."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        host = bind_host(args.host)
    except ValueError as exc:
        parser.error(str(exc))
    if args.port < 0 or args.port > 65535:
        parser.error("port is out of range")
    try:
        serve(host, args.port)
    except OSError as exc:
        print(f"Could not listen on 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
