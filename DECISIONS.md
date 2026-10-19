# GeoDiscovery - Architectural Decision Records

## ADR-001: Product moat is recursive discovery quality
- **Status:** Accepted.
- **Decision:** Build a source-independent geospatial coverage planner, quality evidence system and reliable provider adapters before generic lead-scoring or AI chat.
- **Reason:** Returning >120 raw records is not equivalent to finding all matching places.

## ADR-002: Python modular monolith, Go scraper out of process
- **Status:** Accepted.
- **Decision:** FastAPI + PostGIS + Celery backend and Next.js frontend; invoke gosom through a container adapter if benchmarked positively.
- **Reason:** Fast iteration and strong GIS libraries without maintaining a second core domain model in Go.

## ADR-003: Two distinct recursive collection paths
- **Status:** Accepted.
- **Decision:** Heuristic/adaptive provider sampling for UI-type scrapers, and opt-in count-guided recursive research for Google Aggregate API.
- **Reason:** A result cap detector alone cannot establish recall; the official count path answers a different, narrower question.

## ADR-004: Official Google APIs isolated by source policy
- **Status:** Accepted.
- **Decision:** Google Aggregate/Places data is research-only until permissions/retention/export use are reviewed; the platform must support source-specific retention.
- **Reason:** Official content cannot automatically be cached indefinitely, combined or resold.

## ADR-005: Prefer a mock provider before live scraping
- **Status:** Accepted.
- **Decision:** All planning and coverage logic must pass deterministic fixture tests before any external credentials are added.
- **Reason:** Prevents cost surprises and makes algorithmic behavior measurable.

## ADR-006: Single managed provider first
- **Status:** Accepted.
- **Decision:** First commercial adapter is Apify `compass/crawler-google-places`, not an unverified private Google endpoint or an obsolete actor.
- **Reason:** Verified API and input schema, existing native geographic division and bounded vendor execution.

## ADR-007: Quality reports do not promise 100% completeness
- **Status:** Accepted.
- **Decision:** Report exact discovered counts, unresolved cells and sample-based evidence; allow narrower official-filter completeness language only where justified.
- **Reason:** Google ranking, geometry differences, category ambiguity and incomplete reference sources introduce uncertainty.

## ADR-008: Prove the per-search cap splitter before the application shell
- **Status:** Accepted.
- **Decision:** The first code is `experiments/coverage`, an offline quadtree with a capped mock search. FastAPI, Next.js, and the rest of `BUILD_PLAN.md` wait. Apify is not the fetch step: its Maps actor already subdivides a region. The next live fetch, when Docker exists in WSL Ubuntu, is gosom used as one view per cell that this planner chooses.
- **Reason:** The product risk is coverage past one map-search slice. Buying a finished Apify list does not test or own that splitter. A web stack cannot show whether the splitter found the places a single 120-row page dropped.

## ADR-009: gosom is one view per planner cell
- **Status:** Accepted.
- **Decision:** `experiments/coverage` calls gosom once per cell the planner selects. The command is `-geo lat,lon`, `-zoom` fitted to that cell, `-depth` 1–20, `-c 1`, and `-json`. `-grid-bbox`, `-grid-cell`, and `-fast-mode` are rejected by the argument builder. Email and review crawling stay off. `-exit-on-inactivity` stays off unless a caller passes it: with it set, gosom's 60-second stats tick cancels a scroll that has not finished its first job, which dropped a 117-row feed before any place ID was written. The process timeout is the guardrail. The process returns place ID, longitude, and latitude. The title is kept as a local label for the session view and is not written into committed comparison JSON. Phone numbers and other fields are dropped. The planner clips to the polygon. A repeated view in the same run reuses that page. The live comparison is opt-in, has a hard process budget, and reports completeness as unknown. Proxy URLs stay in a local file such as `secrets/gosom-proxies.txt` and are redacted from errors. Depth 20 is the default because depth 10 returned 75 rows on the central London view, which is under the 96-row split line, while depth 20 reached 117 rows in that same feed.
- **Reason:** ADR-008 keeps subdivision in this planner. gosom's grid would replace that experiment, and fast mode stops near 21 rows. `BUILD_PLAN.md` Phase 5 still mentions `-grid-bbox` for a later full adapter; this experiment does not turn that grid on.

## ADR-010: A local session is the way to watch a split
- **Status:** Accepted.
- **Decision:** `python -m geocoverage.session` is the text check, and `python -m geocoverage.ui` is the localhost page. `--demo` runs the fake 500-place square through `MockSearch`. A live run takes `--lat`, `--lon`, and `--radius-km` (a circle of ground meters, at most 20 km) or a GeoJSON `--polygon`, plus `--keyword`. The planner's first view is the square around that circle. Points outside the circle are dropped. The command prints each piece, the rows, and why it was quartered or stopped, and writes an HTML file under `experiments/coverage/.runs/` that is not committed. The page binds to the loopback addresses `127.0.0.1` and `::1` only, draws those pieces, and can replay the split depth by depth. If the chosen port is already this viewer, the command prints that URL instead of exiting with "address already in use". Its live button uses the same gosom path, with a planner budget of at most 12 searches. The default live planner budget is 5 searches. There is no city-name geocoder. This viewer is not the FastAPI or Next.js application.
- **Reason:** The splitter has to be tried by hand before a product shell exists. A demo path has to work on a machine with no Docker.

## ADR-011: Experiment 1 measures repeatability in Vashi before any action policy
- **Status:** Accepted.
- **Decision:** The first exploratory experiment is repeatability, not coverage. The area is Vashi, not all of Navi Mumbai: a 0.75 km circle around Vashi station (19.0635, 72.9986), split geometrically into the four quadrants of its bounding square (about 750 m per side, zoom 17). Each quadrant is searched 4 times with the same keyword, centre, and zoom, and every repeat is a fresh gosom process through a new `GosomSearch` adapter, because that adapter caches an identical view and a cached page is not a repeat. `geocoverage.repeatability` reports, per cell, the in-target ID union, the core present in every repeat, the best single repeat, extra IDs from repeating, pairwise Jaccard overlap, and rank shift of core IDs. Split/repeat/zoom/shift action comparison (Experiment 2) and any threshold change wait for this evidence. ML stays deferred.
- **Reason:** If one view is unstable, a returned-row threshold is weak evidence and split results are contaminated by that instability. Repeating a small, named locality is the cheapest way to learn whether one search of one cell is a stable unit of evidence.
- **Result (2026-10-19, `restaurant`, 16 fresh searches, `results/vashi_restaurant_repeatability.json`):** In-target ID sets were stable where non-empty — SE core 13/14 (mean Jaccard 0.9643), NW and NE identical sets across all 4 repeats — but rank order was not (core IDs shifted on average 6.7–12.5 positions). Repeating added **0** new in-target IDs beyond the best single search in any cell, so repetition is not a discovery action for this setup; observed churn lives outside the target. The SW quadrant returned 0 in-target IDs in all 4 repeats despite 87–96 raw rows: a sparse cell's feed fills from outside the cell, so raw row count alone must not drive split decisions. Every cell also carried 1–10 far-outside rows per page, including a recurring tail near (-77.48, 39.04); polygon clipping removes them, but they inflate raw counts. Next is Experiment 2 (split vs zoom vs shift on matched cells), with in-target counts — not raw rows — as the compared quantity.

## Open items requiring measured decisions

- Geography/boundary reference provider and commercial use license for each target country.
- Min/max runtime budgets and acceptable per-location density sampling.
- Whether Overture data alone gives enough independent benchmark reference coverage in first target cities.
- Apify versus gosom economics for 3 density classes, measured with real billing and qualified results.
- Production licensing of Google's official API content for the planned downstream uses.
- How to define confidence levels from observed sample recall without presenting unjustified numeric certainty.
