# GeoDiscovery - Coverage Evaluation and QA Plan

## 1. What we must demonstrate

**A collector exceeding 120 returned rows is not enough.** We must independently verify that the planner searches the entire intended geometry, discovers progressively more unique businesses, deduplicates correctly, classifies categories accurately, and reports the uncertainty and cost of any remaining gaps.

There are four different quality metrics:

1. **Geometry correctness:** Every returned in-territory record is actually located in the requested region, given the business coordinate and the region boundary.
2. **Category precision:** Every included record matches the requested type under documented primary/secondary/synonym semantics.
3. **Observed discovery recall:** Fraction of a *controlled reference set* detected by this job. For manually verified samples, this is measured; for Google's entire inaccessible corpus, total recall is unknown.
4. **Operational completion:** Fraction of scheduled tasks processed. This is not a data completeness measure.

Use these terms separately in the UI, API and exports.

## 2. Synthetic reference fixtures (offline, deterministic)

Build generator `tests/fixtures/generate_regions.py` that creates a polygon and explicit source IDs, categories and coordinates with fixed pseudorandom seeds. Add variants:

| Fixture | Test situation | Expected behavior |
|---|---|---|
| `empty_region` | 0 businesses | Clean completion; zero results. |
| `sparse_20` | 20 businesses in large area | No unnecessary recursive blowup, precise geofence. |
| `one_tile_120` | 120 businesses in one search window | Cap proximity triggers investigation rather than claiming completeness. |
| `urban_500` | 500 businesses; first result window returns 120 | Recursion returns all 500 IDs when provider is fully observable at finer cell sizes. |
| `hidden_70_of_150` | Provider returns 70 ranked records for a tile containing 150 | A non-cap observation is not treated as proof; probes or reference checks expose risk. |
| `offset_recovery` | Changing search origin reveals a new subset | Marginal new-ID gain recorded correctly. |
| `border_cases` | 25 points on tile/territory edges | Deterministic ownership; no missing/duplicate output rows. |
| `holes_islands` | Polygon with hole and 2 islands | Coverage follows actual MultiPolygon, excludes hole. |
| `ambiguous_categories` | Restaurants, cafes, suppliers, shared secondary categories | Correct primary-only and any-category results. |
| `chains` | Ten franchise branches share domain/phone | Do not merge into one place. |
| `identical_names` | Unrelated places share brand name | Conservative duplicate handling. |
| `unstable_rank` | Ranked response changes by seed/offset | Do not report false exact completeness. |
| `min_area_105` | 105 POIs in Aggregate cell too small to subdivide | `UNRESOLVED_MIN_GEOMETRY`. |
| `aggregate_100` | Official count equals 100 and IDs present | All 100 eligible Place IDs recorded. |
| `aggregate_101` | Official count equals 101 and no IDs | Splits if possible; otherwise unresolved. |
| `aggregate_parent_500` | Root count >100 | Recursion terminates in valid ID-returning leaves, accounting for overlap. |
| `provider_error` | 429, 503, timeout or malformed response | Bounded retry/circuit breaker and transparent failed cell. |
| `budget_exhaustion` | Runtime cost cap hit mid-run | Stop new work, persist partial dataset + explicit warning. |
| `duplicate_webhook` | Same actor event delivered 3 times | Exactly one normalized ingestion and one charge ledger event. |
| `crash_resume` | Kill worker after provider response, before commit | Recovery without duplicate job-match rows or unaccounted charges. |

Assertions on mock test fixture `urban_500` should compare **sets of IDs**, not just result counts.

## 3. GIS property tests

Use Hypothesis + Shapely + PyProj and PostGIS integration tests:

- Union of clipped child geometries covers parent's valid geometry to a defined small numerical tolerance.
- Child count/depth/area monotonicity, no negative or non-finite area.
- Points inside polygon assigned; holes excluded; shared boundaries do not double count source IDs.
- MultiPolygon islands, enclaves, antimeridian and high-latitude behavior tested; unsupported geometries must fail explicitly.
- Verify GeoJSON `[lon, lat]` versus Google `{latitude, longitude}` conversion using obvious asymmetric coordinates.
- Aggregate polygon validation: <=7,000 vertices, correct CCW winding, first/last closed, no self-intersections, min 1556.86 sq m, max 2 trillion sq m.
- Geodesic or appropriate projected area is used; no treating degrees as kilometers.

## 4. Controlled real-world validation

Benchmark dimensions: **3 densities x 3 categories x at least 2 providers**, e.g. urban core / medium suburb / rural, restaurants / dentists / automotive repair, in a region with trusted administrative boundaries.

Run protocol:

1. Freeze boundary polygon and version, taxonomy choices, locale, query variants and capture date.
2. Run provider A native region search under a fixed budget.
3. Run provider B grid search in the exact same polygon under a comparable or explicitly reported different budget.
4. Run a limited number of offset/subcategory probes on selected cells.
5. Use Overture and/or manually located POIs as **independent leads for validation**, not as definitive Google ground truth.
6. Manually audit stratified samples: high-density cells, boundary cells, isolated/outlier POIs, each status and category ambiguity.
7. Repeat a subset of searches on a different day to estimate stability and newly surfaced IDs.
8. Report costs from actual provider billing events, not just advertised price tiers.

Recommended benchmark result table:

| Region | Category | Provider | Raw rows | Unique IDs | In bounds | Category correct | New from probes | Unresolved area/cells | Billed USD | USD / 1k qualified |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... |

A provider with 20% more raw rows but 35% more off-area duplicates may be worse than a provider with fewer, better-targeted records.

## 5. Coverage metrics

**Do calculate:**

- `recall_on_labeled_sample = verified_matches_found / all_verified_matches_in_sample`.
- `precision_on_labeled_sample = verified_matching_rows / all_reviewed_included_rows`.
- `duplication_fraction = (raw_rows - unique_source_ids) / max(1, raw_rows)`.
- `out_of_area_fraction = excluded_by_precise_geometry / max(1, rows_with_geometry)`.
- `probe_gain = newly_discovered_source_ids / max(1, old_unique_source_ids)`.
- `cost_per_qualified_1000 = 1000 * total_cost / max(1, category_verified_in_territory_unique)`.
- `unresolved_cell_share = unresolved_cell_area / requested_area`, with proper union area so overlaps are not double counted.

**Do not calculate or display as fact:**

- `100% of Google businesses extracted` because every cell was visited.
- `(collected / Google Aggregate parent count)` as an exact recall metric when category definitions, intersecting feature geometries or timing differ.
- `precision` inferred from a confidence model without manual labels.
- A universal "120 per Maps API call" cap; API methods differ.

## 6. Acceptance scenarios for release

### GATE A - Algorithmic correctness

- Synthetic `urban_500` returns exactly 500 IDs in ideal discoverable mode.
- Synthetic `hidden_70_of_150` does not claim verified complete.
- Every partial/failed/blocked/minimum-area condition maps to a visible terminal reason.
- Overlaps never increase unique ID count incorrectly.

### GATE B - Platform reliability

- Worker restarts leave zero lost already committed observations.
- Duplicate callbacks do not duplicate ledger entries, jobs or export rows.
- Per-job spend cap is respected in task scheduling; vendor-native limits applied where available.
- Tenant isolation tests pass for API, pagination, artifacts and downloads.

### GATE C - Quality and cost evidence

- Real provider results include actual source IDs, normalized geography and timestamps.
- Manual sample of at least 100 records from each chosen benchmark density/category combination unless unavailable; report confidence intervals only when sampling design justifies them.
- Store reproducible job manifests: polygon version, category versions, provider builds, settings, date, budgets and measured charges.
- Publish provider comparison with uncertainty and limitations rather than a victory claim.

### GATE D - Rights and data retention

- Unit and integration tests prove expired source data is purged on schedule.
- Export rejects restricted fields and applies source attribution where required.
- Official Google research adapter cannot publish prohibited output from an unrestricted API endpoint.

## 7. Performance and scale tests

- 100,000 mock observations ingested through streamed batches without unbounded memory growth.
- 10,000 records exported from DB into CSV, JSONL and Parquet without loading entire result set.
- Concurrent worker cost reservation: no double-booking of the same budget unit in races.
- Region lookup and `ST_Covers` spatial filtering use indexes; inspect SQL query plans.
- Dynamic subdivision queues prioritize densest/highest-uncertainty cells without starving sparse work.
- Persistent retry exhaustion cleanly produces `PARTIAL` plus actionable provider/task errors.

## 8. Research questions to measure, not guess

1. Does Apify Compass native region partitioning find more correct in-area records than a manual uniform grid at the same dollar budget?
2. What share of seemingly unsaturated tiles yield new IDs when searched from shifted centers or nearby category variants?
3. How closely do Aggregate counts match discoverable Maps UI records after harmonizing region, date, status and category semantics?
4. At what density does recursive official Aggregate collection hit its minimum-area constraint?
5. Which provider returns reliable secondary categories, status, place IDs, phones and hours, and at what incremental price?
6. How does the optimal cell size and provider choice change across rural, suburban and city-center regions?
7. When are missing places a provider failure versus Google indexing, category assignment or coordinate/geographic boundary differences?
