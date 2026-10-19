# Instructions for coding agents working on GeoDiscovery

## Current focus (2026-10-19)

The active task is the per-search cap, not the application. One Google Maps view returns a ranked slice of about 120 places. A larger area does not raise that slice. The work is to split a polygon into smaller views, search each view, dedupe by place ID, clip to the polygon, and mark cells that are still uncertain.

Do not start `BUILD_PLAN.md` Phase 0. Do not scaffold FastAPI, Next.js, Celery, or PostGIS. Do not call Apify. Apify `compass/crawler-google-places` already splits a region and returns a finished list, so calling it replaces this experiment instead of testing it. The user rejected that path on 2026-10-19.

Code that exists:

- `experiments/coverage/` — Python package `geocoverage`. `MockSearch` returns at most `cap` ranked places per view. A `hidden` mode drops a seeded subset even when the page is shorter than the cap.
- `GosomSearch` in `experiments/coverage/src/geocoverage/gosom.py` is the live one-view adapter. One process gets `-geo` latitude,longitude, a zoom fitted to that cell, `-depth` at most 10, and `-c 1`. The argument builder rejects `-grid-bbox`, `-grid-cell`, and `-fast-mode`. Email and review crawling stay off. Parsed rows keep place ID, longitude, and latitude. The planner still clips to the polygon and still withholds a completeness percentage.
- Location input is a Shapely `Polygon` or `MultiPolygon` in `(longitude, latitude)` order. There is no city-name or address input. The offline fixture area is `box(0.0, 0.0, 0.02, 0.02)` in `experiments/coverage/src/geocoverage/fixtures.py`. The opt-in live polygon is `experiments/coverage/regions/central_london.geojson`.
- `discover()` in `planner.py` splits when a view returns at least 80% of that adapter's cap, probes quiet cells, and never emits a completeness percentage. The gosom cap constant is 120.
- Verified on 2026-10-19 from `experiments/coverage`: `uv run pytest -q` passed 30 tests, `uv run ruff check src tests` passed, `uv run mypy src` passed. `uv run python -m geocoverage` still reports the mock fixture: one search of 120 IDs and a planner result of 500 IDs, with 380 IDs only in the planner.
- `hidden_70_of_150` stays `UNRESOLVED` with `RANKING_RISK`. A one-search budget stops at 120 IDs with `BUDGET_EXHAUSTED`.

Docker:

- WSL Ubuntu 26.04 was not changed. This session is a separate Ubuntu 24.04 machine, not that distro. `docker.io` 29.1.3 is installed here. systemd is offline, so the daemon was started with `dockerd --iptables=false --ip6tables=false`.
- The WSL check from 2026-10-19 still stands for that machine: `wsl -d Ubuntu` had no `docker` or `go` binary. The user is in group `docker` and `~/.docker` remains from an old Docker Desktop setup, but that engine was not running. Windows PowerShell also had no `docker` or `go`.
- Proxy URLs belong in `secrets/gosom-proxies.txt` (gitignored). No proxy file was added.

Next task:

1. Run one small real comparison once `gosom/google-maps-scraper` is pulled: `uv run python -m geocoverage.live --keyword coffee --polygon regions/central_london.geojson --max-searches 5` from `experiments/coverage`. Fast mode stays off. The fetch budget is 5 processes. Compare place-ID sets. Completeness stays unknown.

Decision record: ADR-008 in `DECISIONS.md`.

## Mandate

Your task is to build the geographic POI discovery product specified in `PROJECT_SPEC.md` according to `BUILD_PLAN.md` and `QA_PLAN.md`. The central technical objective is **high-coverage recursive discovery with evidence of remaining uncertainty**, not a simple script that returns the first 120 rows.

## Before editing

1. Read all three documents and `DECISIONS.md`.
2. Identify the current phase and acceptance tests.
3. Inspect existing code and tests; preserve working code.
4. State any relevant ambiguity as an issue, but pick the documented default where one exists.
5. Start with an offline deterministic mock provider; external billable calls are off by default.

## Implementation practices

- Modular monolith: FastAPI, PostGIS, Celery/Redis and Next.js. Avoid unnecessary microservices or frameworks.
- Strict typing: Pydantic schemas for API/provider boundaries, typed Python, strict TypeScript.
- Every geographic operation has explicit SRID and units. GeoJSON coordinate arrays use `[lon, lat]`; Google service structures may use `{latitude, longitude}`.
- Keep provider adapters thin. No provider-specific response shape should leak into core planner logic.
- Keep the planner deterministic with injectable provider, clock, seeds and budget limits.
- Exact source ID matching first; fuzzy duplicate recognition produces a candidate, not an automatic merge.
- Never infer completeness from `results < 120`.
- Do not silently swallow malformed geometry or provider schema fields.
- Every external operation must have timeouts, bounded retries, tracing, authentication and rate/cost guardrails.
- Enforce retention/export policy per source at data insertion **and** export time.
- Make routes idempotent where job creation, worker retries, callbacks or billable work are involved.
- Maintain backward-compatible migrations and never delete a user's valid work without an explicit migration plan.
- Do not hardcode cloud credentials or use unnecessary personal data.
- Use modern stable dependency versions, pin lockfiles, and explain major library choices in `DECISIONS.md`.

## External providers

- Apify actor: `compass/crawler-google-places`. Use latest documented input schema. The correct limit is `maxCrawledPlacesPerSearch`, not `maxCrawledPlaces`. Apply actor-run cost caps.
- gosom scraper: MIT-licensed separate Go process; grid search must be post-filtered to exact polygon.
- Google official Aggregate, Nearby, Text and Details: **research adapter only** until specific terms and permissions permit a particular production use. Place IDs are a distinct exception to Google cache restrictions; other content generally is not unrestricted.
- No automation to circumvent authentication, CAPTCHA or API quotas by account/project proliferation. Handle blocks with safe error states and human review.

## Testing expectations

- Unit tests for every nontrivial planner/geometry/data normalizer function.
- Property-based tests for tiling, polygon unions, category filters and deduplication.
- Offline provider fixture tests for 0, 1, 20, 60, 99, 100, 101, 120, 121 and 500 results, plus deliberately hidden/ranked results.
- Integration tests for PostGIS transactions, queue crashes, idempotency, budget reservation and export gating.
- E2E browser tests for the main mock-provider journey.
- Paid provider tests are explicitly opted into and have a budget cap.

## Definition of done for each phase

Report:

1. What changed, with file paths.
2. Tests, lint, type checks and startup commands actually executed, plus results.
3. Whether all the phase's acceptance tests pass.
4. Source policy/security implications.
5. Any incomplete or blocked items; do not claim they are done.
6. Updated `DECISIONS.md` when deviating from a specification.

Do not mark a milestone complete merely because code was generated. Demonstrate the requested behavior with tests and observed output.
