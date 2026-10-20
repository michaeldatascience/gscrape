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

## Experiment 3 — second-level split (16 grandchildren, ~187.5 m, best cell per locality)

- London SE: baseline 186 → **380** (+194 new, 12.1/search). Every
  grandchild page returned 45–113 in-target places. Recursion is nowhere
  near dry at 187.5 m in dense central London.
- Vashi NE: 45 → 46 (+1 in 16 searches, 0.06/search). Dry.
- Austin SE: 79 → 82 (+3 in 16, 0.19/search). Dry, even though its level-1
  split had found +28.
- Lesson: level-2 yield spans ~200× on identical geometry and keyword.
  Whether to deepen must be decided per cell from what its children
  returned, never as a global depth. A level-1 win does not by itself
  predict a level-2 win.

## Open questions

- In London, does level 3 (splitting the productive grandchildren, ~94 m)
  still pay, and which grandchildren carry it?
- Can a cheap child-level signal (in-target count of the level-1 children,
  which we did not store separately) predict level-2 yield well enough to
  skip dry branches without searching them?
- Does the action ranking hold for a sparser keyword (e.g. `dentist`)?
- What baseline signal best predicts split gain: raw rows, in-target count,
  in-target share, or viewport/cell span ratio?
- The ~120-row cap binds on raw rows, not in-target IDs. What split signal
  should replace "rows ≥ 96" in the planner?
- Why do some pages carry far-away place IDs (the -77.48/39.04 tail)?

## Plain summary (2026-10-20)

### Things that are consistent (seen in all three places, many times)

- One search never shows all the places in an area. Splitting the area
  into smaller pieces always finds more.
- Doing the exact same search again gives almost the same list. The order
  changes, but almost no new places appear. Repeating a search is a waste.
- The size of the list Google returns (about 90-120 names) is not the
  number of places in your area. Many names on the list are outside it.
- Splitting is the best way we found to get more places.
- Some areas are simply empty for a word. They stay empty no matter what
  we try.

### Things that are likely (seen, but tested less)

- Zooming in helps when the first view was too wide for the piece (it
  helped a lot in London). When the view already fits the piece, it
  helps little.
- Moving the centre a bit helps sometimes, but splitting usually finds
  the same places anyway.
- Splitting a second time only helps in very busy places (London). In
  normal places it mostly wastes searches.
- The busier a place is, the more a single search misses.

### Things that are unreliable (more testing needed)

- The exact rule for when to stop splitting. We do not have a number yet.
- Other search words. We only tested "restaurant".
- Other kinds of areas: small towns, countryside, other countries. We
  tested three busy city centres only.
- Why some lists include places from very far away. Seen, not understood.
- How many places exist in total in any area. We never know this. Every
  number we report is "found so far", not "all of them".
- Whether a third level of splitting helps even in London.

## Budget rules

- Cost so far: 168 searches, about 2 minutes each, across Vashi, London,
  and Austin (Experiments 1-3). There is no API fee; the cost is time.
- Every run gets a search cap before it starts, written in its plan.
- One run answers one question. No "just in case" extra cells.
- Old results are reused (re-analyse) instead of searched again.
- If a whole round of splitting adds almost nothing (as in Vashi and
  Austin at level 2), that branch stops. We do not split it again.
- New ideas are tested on one small area first, then widened only if
  they pay.
