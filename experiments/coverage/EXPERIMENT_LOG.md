# Experiment log

Running notes from the exploratory phase. Each entry says what was run, what
was observed, and what it changes. Numbers come from the JSON files in
`results/`; full search runs stay in the gitignored `.runs/` folder.
Nothing here claims a total number of places for any area.

## Setup shared by Experiments 1–2

- Keyword `restaurant`; a 0.75 km circle; the four ~750 m quadrants of its
  bounding square are the cells. Centres: Vashi station (19.0635, 72.9986,
  zoom 17), Central London (51.514, -0.1325, zoom 16), Austin downtown
  (30.2672, -97.7431, zoom 17). Zoom comes from the span fitter, so London's
  view is physically wider (~1.19 km) than the others (~0.83–0.90 km).
- gosom one view per search, `-c 1`, depth 20, ~110–150 s per page.

## Experiment 1 — is one view repeatable? (4 fresh repeats per cell)

- ID sets are stable: mean pairwise Jaccard 0.988 (Vashi), 0.974 (London),
  0.988 (Austin). Rank order is not: core IDs shift 2.4–38.9 positions.
- Repeating added **0** in-target IDs beyond the best single search in all
  12 cells. Repeat is not a discovery action in this setup.
- Raw rows do not measure local density. London pages returned 117–120 raw
  rows with only 32–55 in-target. Vashi SW returned ~90 raw rows and 0
  in-target in every repeat.
- Oddity: some pages carry a tail of place IDs near (-77.48, 39.04) at high
  ranks. Clipped by the polygon filter; noted as scraper noise to watch.

## Experiment 2 — split vs zoom-in vs shift (against first-repeat baselines)

- Observed IDs after all actions: Vashi 87→108 (+24%), London 153→622
  (+307%), Austin 245→288 (+18%). One search misses most exactly where
  density is highest.
- Split (4 pages) found the most new IDs in every locality and had
  exclusive finds in most cells. Zoom-in was cheapest per find only where
  the baseline zoom overshot the cell (London: 62–76 new per single page).
  Shift was weakest and mostly a subset of split.
- Vashi SW stayed at 0 in-target across both experiments (10 searches,
  3 action types): a cell can be genuinely empty for a keyword.

## Soft conclusions so far (ADR-013)

1. Never repeat an identical view to discover places.
2. Split is the default discovery action; zoom-in first when the fitted
   viewport is much wider than the cell.
3. Judge cells by in-target IDs, never raw rows.
4. Park a cell that stays at 0 in-target; do not keep spending on it.

## Open questions

- Does a second level of splitting (187.5 m cells) keep finding places, or
  does the gain dry up? → Experiment 3.
- Does the action ranking hold for a sparser keyword (e.g. `dentist`)?
- What baseline signal best predicts split gain: raw rows, in-target count,
  in-target share, or viewport/cell span ratio?
- The ~120-row cap binds on raw rows, not in-target IDs. What split signal
  should replace "rows ≥ 96" in the planner?
- Why do some pages carry far-away place IDs (the -77.48/39.04 tail)?
