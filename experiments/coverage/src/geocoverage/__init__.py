"""Recursive map-search coverage experiment.

One search returns a ranked slice. This package splits a polygon until each
slice fits under that cap, dedupes by place ID, and keeps points inside the
requested polygon. A quiet page is not treated as a complete census.
"""

from geocoverage.compare import compare_searches
from geocoverage.gosom import MAPS_VIEW_CAP, GosomSearch
from geocoverage.planner import discover
from geocoverage.search import MockSearch, Place

__all__ = [
    "MAPS_VIEW_CAP",
    "GosomSearch",
    "MockSearch",
    "Place",
    "compare_searches",
    "discover",
]
