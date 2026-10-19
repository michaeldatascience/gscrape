# GeoDiscovery - Algorithm Design Notes for the Coding Agent

**Purpose:** Implementation-level guidance for the two recursive discovery approaches, exact geometry handling, matching and quality audits. The methods below are engineering recommendations, not guarantees about any provider's coverage.

## A. Geometry model and coordinate contract

- Authoritative region: versioned PostGIS `geometry(MultiPolygon, 4326)`; GeoJSON uses `[longitude, latitude]`.
- Always validate `ST_IsValid`, `ST_Area` via appropriate geography/projected CRS, bounds and coordinate ranges.
- For a chosen local region, transform geometry to a locally appropriate equal-area CRS or use geodesic operations for measurements; transform resulting partitions back to WGS84.
- Store per-cell `cell_geom`, `effective_geom = ST_Intersection(cell_geom, requested_region_geom)` and `search_geometry` (which may need buffer or decomposition for provider compatibility).
- MultiPolygons may contain holes or islands. Never assume a place returned by a provider is inside the requested boundary until checking its point.
- Use `ST_Covers(requested_region_geom, place_point)` for an inclusive point-in-polygon test. Points on shared cell borders can appear twice, so result identity is still deduped by a stable source key.
- A provider may return a chain's place whose coordinate is in a different neighborhood despite the search term. Accept this in the raw observation stream but exclude it from the in-territory result set.
- Protect against invalid shapes, rings with wrong winding, narrow slivers, dateline crossing and lat/lon inversion.

## B. Choosing tile geometry

### Initial partition

1. Resolve a well-defined region polygon. For a large country, optionally split by its constituent administrative regions first.
2. Approximate urban density using one or more of: a small pilot search, independent Overture POIs, population-density data, or historical job evidence. Every estimate is a hint, not Google ground truth.
3. Choose initial tiles so each provider query has a plausible chance of returning well below its *empirically measured* limit. Use 0.5-1.0 km tiles as a starting benchmark in dense cities, but adapt to provider response and location.
4. Generate a deterministic cell key (`job_uuid`, `parent_path`, `split_version`, `search_variant`, `provider`) so retries can be recognized.
5. Clip candidate cells to the requested MultiPolygon; discard only geometrically empty intersections, not cells with 0 expected businesses.

### Splitting

Start with projected quad splitting: subdivide a rectangle into four non-overlapping rectangles, intersect each with the parent effective geometry, then create provider-valid search footprints. This is easier to reason about than recursively subdividing H3 hexes, which do not form exact hierarchical geometric partitions. H3 may still be useful for density analytics and caching.

If official Aggregate geometry minimum area disallows a small clipped child, either cover that fragment with a valid overlapping query shape and dedupe, or classify the fragment as unresolved. Do not silently drop it. If provider requires simple polygons, decompose MultiPolygon/holes without losing the original geometry for final filtering.

## C. Adaptive grid collector: explicit priorities

### Observation signals per cell

```text
returned_rows
unique_source_ids_in_cell
new_global_ids_added
empirical_provider_cap_for_query_type
error_rate
retry_count
overlap_with_neighbor_observations
edge_clustering_ratio
qualified_category_match_ratio
independent_reference_unmatched_candidates
marginal_new_ids_from_probes
```

Define a `cell_risk` record with components, not only an opaque 0-1 score:

- `cap_risk`: observed rows close to a provider search cap that has actually been calibrated.
- `ranking_risk`: independent offset/repeat probes return previously unseen IDs.
- `boundary_risk`: significant returned entities lie near tile boundaries or outside the original region.
- `reference_risk`: Overture or labeled local businesses in the cell were not discovered; their absence is a candidate gap, not proof.
- `taxonomy_risk`: provider's first-page sample contains an unusually high share of wrong or ambiguous categories.
- `execution_risk`: partial page loading, HTTP errors, timeouts, provider limits.

### Proposed MVP decision policy (tunable, not universal)

1. If `execution_risk` is present, retry only transient errors within capped attempts, then mark unresolved.
2. If row count is >= 80% of a known source-specific result cap, split when possible. This 80% threshold is a *starting heuristic* and must be calibrated.
3. If a valid probe returns materially new IDs, split and/or probe further within budget; start by flagging an added-ID gain >2% or >=3 new IDs in a small cell.
4. Stratified random audit: probe at least 10% of low-risk cells using a stable seeded sample so unobserved misses have a chance to be detected. Calibration may later revise this fraction.
5. If no additional query is affordable or valid, close as `UNRESOLVED` with a reason, not `COMPLETE`.
6. If all planned queries and audits finish, close as `HIGH_EVIDENCE` only with a coverage report that explains remaining unknowns.

### Suggested priority queue ordering

Use weighted priority by evidence, not purely cell size:

```text
priority =
    5.0 * is_known_saturated +
    3.0 * has_unmatched_reference_candidates +
    2.0 * has_positive_probe_gain +
    1.0 * normalized_border_density +
    1.0 * normalized_historical_density -
    0.5 * normalized_projected_cost
```

These coefficients are defaults for research. Preserve the component values in the DB and measure how changing them affects recovered IDs/$, before using them in a production policy. A starving queue should increase old-task priority over time.

## D. Adaptive grid workflow with precise state transitions

```python
async def process_cell(job_id, cell_id):
    cell = await cell_repo.claim_for_update(cell_id)
    if cell.status in TERMINAL_STATES:
        return

    await budgets.reserve(job_id, estimated_provider_charge(cell))
    try:
        run = await provider.start_search(task_from_cell(cell))
        await provider_run_repo.record(run)
        async for observation in provider.iter_observations(run.id):
            await observations.append_idempotently(observation)
        await budgets.reconcile(run.billing)
        metrics = await audit.cell_metrics(cell_id)
        decision = split_or_probe_or_close(metrics, job.policy)
        await apply_decision_transactionally(cell, decision)
    except TransientProviderError as exc:
        await schedule_bounded_retry(cell, exc)
    except InvalidGeometryError as exc:
        await mark_unresolved(cell, 'INVALID_GEOMETRY', exc)
    except BudgetExceeded:
        await mark_unresolved(cell, 'BUDGET_EXHAUSTED')
    finally:
        await budgets.release_unused_reservation(job_id, cell_id)
```

**In a real implementation:** external actor start/cancel is not part of the DB transaction; use a persisted outbox/lease model to avoid duplicate paid launches after process crashes. Before retrying creation, query stored `provider_run_id` and external run status; never blindly repeat a paid start call.

## E. Official Aggregate recursive ID discovery

For each exact supported category/status filter:

1. Normalize the region to a provider-compatible shape. Break country-sized and multi-component geography into valid shapes under the maximum area and vertex limit.
2. Request both `INSIGHT_COUNT` and `INSIGHT_PLACES` together where appropriate. Capture source query spec, timestamp and cost.
3. If count <=100 and ID list is present, the cell's source-filter-eligible IDs are acquired.
4. If count >100, split area into valid parts and recurse.
5. If unable to split because every child would be below 1556.86 square meters, choose a safe overlap strategy only if possible; otherwise stop with `UNRESOLVED_MIN_GEOMETRY`.
6. Distinguish records duplicated across overlapping search areas and records with no point inside the user's territory.
7. Compare direct parent and child evidence cautiously. Google's region approximation and intersection-based inclusion for polygonal places can produce non-additive counts.
8. Require explicit policy/terms approval before using counts for a purpose beyond allowed research/analytics or storing/exports of other Google content.

**Optimization opportunity:** Spatial splitting can be binary along the longest metric dimension or quadtree. Benchmark requests required per 1000 collected IDs. An adaptive density-balanced cut can reduce call count relative to blindly splitting four quadrants, but costs more to compute. Keep the splitter pluggable (`quadtree`, `longest_axis_binary`, `density_weighted_binary`).

**Critical question:** Is every set of 100 IDs from each child sufficient to reproduce exactly the parent set? Often overlap/geometry/status/categorization differences mean you must audit this empirically. Do not present parent-child count sums as proof without a matched model.

## F. Identity normalization and fuzzy matching

Source identity priority:

1. Same source and same source place ID: exact same source entity.
2. Documented source-ID migration/alias: linked, with evidence.
3. Across sources: plausible matching entity based on address, geo distance, normalized name, phone, website *and* category. Require multiple signals.
4. Same brand or same website without address/geo match: likely different branch; do not merge automatically.

Design a reversible `merge_decisions` journal with `decision`, `operator`, `evidence`, `made_at`. Training an ML entity-matcher can wait. An `uncertain` match remains a separate record but surfaces to a review queue.

Use source-specific data fields; `null` means unknown/unavailable and must not be interpreted as absent. Phone and website normalization do not imply verified ownership.

## G. Details collection scheduling

- Discovery stage should favor cheap place IDs, names, coordinates and categories where supported.
- Before standard/extended details, calculate per-provider cost by field package; prefer a *single* allowed details fetch for all required fields if the pricing model supports it.
- Work only on deduplicated, in-area, category-correct source records, unless a requested field is necessary for matching (then make that dependency explicit).
- Maintain independent detail status: `NOT_REQUESTED`, `QUEUED`, `FETCHED`, `PARTIAL`, `UNAVAILABLE`, `FAILED`, `POLICY_RESTRICTED`.
- Do not assume every Maps UI data field has an official API equivalent. Rich details vary by provider, country, entity and plan.
- For live Google data, use current field-mask SKU tables; phone and website are not automatically included in a cheap basic/details request.

## H. Refresh and change detection

On a rerun over the same versioned territory/category specification:

- `newly_observed`: absent from previously stored *permitted* observation set, present now. Not proof of a recent opening.
- `not_seen_this_run`: previously observed, absent now. Not proof of closure; requires verification.
- `changed_fields`: supported comparable values differ, with timestamps and provider metadata.
- `closed_confirmed`: explicit source status indicates closure, not simply missing in search.

Schedule missing records for a targeted recheck under a separate budget, before claiming significant business changes. Distinguish true location change from Google identifier alias/renaming.

## I. Query cost optimization formula

```text
incremental_yield(task) ~= estimated_new_qualified_ids(task) / estimated_provider_charge(task)
```

Prioritize informative tasks rather than uniformly oversampling. Estimate yield from completed nearby cells, previous variants, Overture density and probe history. Forecast uncertainty with intervals, not single-point guarantees. Save observed actual new IDs and cost after every task to recalibrate.

The true competitive advantage should be **higher verified in-region unique IDs per dollar at a measurable quality standard**, plus clear explanations for where no collector can safely claim exhaustive coverage.
