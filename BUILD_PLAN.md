# GeoDiscovery - Coding Agent Implementation Plan

**Document version:** 1.0 (2026-10-17)  
**Rule:** Build sequentially. Do not skip a release gate or implement a phase's dependent features before prerequisites work.

## Operating instructions

- First read `AGENTS.md`, `PROJECT_SPEC.md`, `QA_PLAN.md`, `openapi.yaml` and `REFERENCES.md`.
- Keep the application executable after every phase.
- The initial end-to-end path uses fixture data, not real scraping credentials.
- Record new or changed design decisions in `DECISIONS.md`.
- At the end of each phase: run tests, describe changed files, include commands and evidence, list risks/limitations, then continue only when prior acceptance criteria pass.
- No external provider calls in CI. Integration tests with billable providers require `RUN_PAID_INTEGRATION_TESTS=true` and a cost cap.

## Phase 0 - Repository and architectural decisions

**Goal:** Ensure the repo can run locally without external API keys.

Tasks:

1. Scaffold `services/api` Python 3.12/FastAPI, `apps/web` Next.js TypeScript, root Makefile/Taskfile and Docker Compose.
2. Set up Postgres/PostGIS, Redis, MinIO and Celery worker, with health checks.
3. Add Python `uv` lockfile, JS `pnpm` lockfile, CI lint/tests and pre-commit.
4. Add environment parser; `.env.example` contains placeholders only.
5. Implement health/readiness endpoints and database migration command.
6. Document cloud/local startup including Windows WSL2 + Docker Desktop.

**Acceptance:** Fresh checkout, one compose startup, backend health + frontend loads + migrations succeed, all CI checks pass. Nothing attempts to contact Google or Apify.

## Phase 1 - GIS and taxonomy domain

**Goal:** Resolve and validate real polygons and categories before making any searches.

Tasks:

1. Create `geo_areas` schema, `categories`, `category_aliases`, versioned administrative hierarchy.
2. Support Polygon, MultiPolygon, circle and bounding-box requests; limit vertices/payload size.
3. Implement GeoJSON coordinate contract: lon/lat storage, units in meters; automatic geometry validation with informative errors.
4. Add admin selector seeded from an openly licensed/appropriately attributed source; don't hammer Nominatim public service.
5. Implement projected tiling with parent-child cells, intersections, holes/islands and deterministic stable IDs.
6. Create a map preview endpoint and a basic frontend region selection/validation screen.
7. Add location source and license metadata.

**Acceptance:** Property-based area coverage tests pass; all points in synthetic fixtures are assigned; geometry holes are respected; large country and antimeridian tests are defined and either pass or clearly marked unsupported with an explicit error.

## Phase 2 - Discovery domain and deterministic mock provider

**Goal:** Prove recursive acquisition with no external dependency.

Tasks:

1. Add `discovery_jobs`, `search_cells`, `search_tasks`, `provider_runs`, `places`, `place_source_ids`, `place_observations`, `job_place_matches`, `quality_audits` migrations.
2. Implement ProviderCapabilities and separate provider/search, count, and details interfaces.
3. Build MockProvider from synthetic fixture points: configurable 120-result cap, ranking bias, random-but-seeded omissions, errors, latency and repeated-search variance.
4. Implement `adaptive_audited` planner, split conditions, probe sampling and budget reservation.
5. Persist exact-ID deduplication, geometry inclusion, status and category precision.
6. Implement Celery planning/search/normalize/audit tasks and idempotent task fingerprints.
7. Expose `/jobs`, `/jobs/{id}`, `/jobs/{id}/cells`, `/jobs/{id}/quality`, `/jobs/{id}/places`.
8. Export manifest + CSV/JSONL.

**Acceptance:** Synthetic polygon with 500 businesses and a 120-record first-search limit results in all 500 IDs for the deterministic, fully observable mock mode; deliberately hidden records appear as uncertainty/unresolved rather than a false "complete" claim; crashes can be resumed without duplicates.

## Phase 3 - UI vertical slice

**Goal:** User can work end-to-end with the mock engine.

Tasks:

1. New job wizard: territory, categories, provider, data depth, spending limits, review/submit.
2. Live job status screen (poll every few seconds), stats and interactive cell layer.
3. Searchable records table with inclusion/exclusion decisions and duplicate indicators.
4. Quality screen showing high-evidence/uncertain/failed areas and unresolved reasons.
5. Export creation/download screen with manifest.
6. Add the first Playwright end-to-end test.

**Acceptance:** A browser user launches, pauses, resumes and exports the synthetic 500-record job; overlay shows all cells and statuses accurately.

## Phase 4 - Apify Compass integration

**Goal:** First real vendor, safely bill-limited.

Tasks:

1. Read the latest actor input schema before implementing. Adapter must reject unknown input keys. Validate `maxCrawledPlacesPerSearch` (not legacy `maxCrawledPlaces`).
2. Configure actor ID `compass/crawler-google-places`; support `locationQuery` or `customGeolocation`, search terms and locale.
3. Implement create-run, poll/webhook, fetch/stream dataset, map normalizer, cancel.
4. Pass native `max_items`/`max_total_charge_usd` limits supported by the current Apify client; enforce separate internal cost reservations.
5. Disable paid enrichment and details add-ons unless field package requires them and user budget allows.
6. Capture actor build, event charges, input hash, collection timestamps, errors and data rights.
7. Run one tiny **explicitly authorized** billable integration test and validate actual returned schema.

**Acceptance:** At least one real bounded region query finishes and exports results within budget, with provider run logs and data provenance; webhook duplication and bad parameters do not duplicate records or charge beyond available budget.

## Phase 5 - Real adaptive auditing and optional gosom

**Goal:** Collectors are judged on missing places, not just output size.

Tasks:

1. Add tile saturation telemetry, additional viewport/offset probes and per-tile marginal-ID gain.
2. Add targeted refinement around high-uncertainty cells instead of always recursively subdividing the whole territory.
3. Add independent reference overlap using Overture Places in a limited region; retain provenance.
4. Create detailed quality report with terminal states, no fabricated recall claims.
5. Add gosom integration as separate CLI/container with `-grid-bbox`, `-grid-cell`, `-zoom`, `-depth`; do not enable `-fast-mode` for deep collection.
6. Support provider A/B runs for same region and category without commingling restricted raw data.

**Acceptance:** The benchmark distinguishes result yield, unique yield, in-boundary precision, category precision and cost per qualified record. A second provider or additional probe discovers some intentionally seeded omissions.

## Phase 6 - Official Aggregate research experiment (gated)

**Goal:** Independently test the <=100 count-guided partitioning method.

Tasks:

1. `RESEARCH_GOOGLE_AGGREGATE=false` by default. Require separate credentials and acknowledgement of policy constraints.
2. Implement query validation for min/max area, closed CCW simple custom polygon and 7,000-vertex limit.
3. Add `computeInsights` client with `INSIGHT_COUNT` and `INSIGHT_PLACES` in one call.
4. When >100, recursively split; when <=100, save permitted IDs. If an area is too dense to split under min-area, label unresolved.
5. Add per-request cost model and hard API budget.
6. Implement Place Details (New) IDs-only test and an optional field-mask-aware details request for temporary research use.
7. Record a report that separates Aggregate-filter completeness from Map UI search coverage, and clearly distinguishes under-100 leaf evidence from global completeness.
8. Verify source policy before introducing any production export or historical persistence.

**Acceptance:** Mocked provider tests at counts 0/100/101/500 pass, minimum-area and polygon errors handled, 100-ID threshold exact; paid live test only if enabled and source usage terms approved. No production path can export unauthorized official data.

## Phase 7 - Data quality and user-facing product features

**Goal:** Move from a scraper experiment to a usable MVP product.

Tasks:

1. Add phone/domain/address normalization, stable source identifiers, fuzzy duplicate candidates and conservative merge review.
2. Add primary/secondary category filter, business status, search term provenance and category confidence.
3. Implement search filters, blacklists/exclusions and XLSX/Parquet (stream and row limits).
4. Add source-specific detail packages, delayed detail lookups, billable field warnings.
5. Add saved searches, schedules, compare-runs/deltas and alertable changes (mark "newly discovered" vs "opened").
6. Add initial multi-tenant authorization, scoped API tokens and a credit/usage ledger; mock payment processing.
7. Add API docs, user-visible data rights and attribution enforcement.

**Acceptance:** A 10k+ record fixture can be filtered/exported with accurate counts and sources, and two workspace users cannot access each other's jobs or exports.

## Phase 8 - Release hardening

**Goal:** An internal beta used on real, narrow geographies.

Tasks:

1. CI for unit/property/integration/E2E tests, DB migrations and secret scanning.
2. Load test 100k record ingestion, 10k record export, and concurrent worker budget enforcement.
3. Add tracing, dashboards and alerts for retries, queue backlogs, costs and provider response drops.
4. Implement source-specific TTL-based raw retention, deletion, provider permission checks, export audit and security review.
5. Run QA matrix: three densities x three categories x at least two acquisition methods. Record actual invoices and provider costs.
6. Publish operator runbook and backup/recovery instructions.

**Acceptance:** Go/no-go memo with observed spatial recall proxies, manually sampled precision/recall, cost per qualified record, unresolved fraction, and no open critical bugs.

## Phase 9 - Beyond Scrap.io (post-MVP)

Prioritize by validated demand:

- Higher-confidence source fusion with Apple/Bing/Overture/OSM and configurable record ownership.
- Reverse lookup by phone, domain, place ID, Maps URL and name + location.
- Longitudinal change detection with evidence, not merely aggregate row comparisons.
- Programmatic coverage planning API and downloadable cell-level reports.
- Optional website enrichment queue, email/social extraction and technology fingerprints.
- MCP/AI agent interface for initiating approved jobs, inspecting coverage and composing exports.
- Automated re-search of sparse/uncertain cells based on measured information gain.
- Team-based SaaS billing, API quotas, credit consumption, reseller rights controls and CRM connectors.

## Task slicing for a coding agent

Each coding pass should complete **one coherent vertical task** with tests, usually touching one domain module plus a migration/API route. Do not ask the agent to implement entire Phase 1-9 autonomously without intermediate verification.

Suggested first coding-agent message:

> Read AGENTS.md, docs/PROJECT_SPEC.md, docs/BUILD_PLAN.md and docs/QA_PLAN.md. Implement only Phase 0 with Docker Compose, backend/frontend scaffolding, PostGIS migration capability, health endpoints and offline CI. Use no billable APIs. Run lint, types, tests and a fresh-start smoke test, then report files changed, commands executed and blocked decisions. Do not proceed to Phase 1 in the same pass.
