# GeoDiscovery - Verified Research References

**Checked:** 2026-10-17. URLs are stable public documentation/repository entry points, but provider input schemas, licenses, pricing and quotas can change. Revalidate before connecting a paid production account.

## Core provider APIs and pricing

1. Scrap.io API v2 introduction: https://docs.scrap.io/ (base API `https://scrap.io/api/v2/`; previous endpoint aliases deprecated).
2. Scrap.io product: https://scrap.io/
2a. Scrap.io pricing and feature tiers: https://scrap.io/pricing
3. Apify Compass Google Maps Scraper: https://apify.com/compass/crawler-google-places
4. Apify Compass actor input schema: https://apify.com/compass/crawler-google-places/input-schema
5. Apify Compass actor pricing: https://apify.com/compass/crawler-google-places/pricing
6. Apify actor API: https://apify.com/compass/crawler-google-places/api
7. Apify Python ActorClient, `max_items` / `max_total_charge_usd`: https://docs.apify.com/api/client/python/reference/class/ActorClient
8. Apify documentation on 120-map results and geographic subdivision: https://blog.apify.com/google-places-api-limits/
9. Apify issue explaining obsolete `maxCrawledPlaces` parameter (use `maxCrawledPlacesPerSearch`): https://apify.com/compass/crawler-google-places/issues/issue-maxcrawledplac-LrnyfGqtxBUNCESnE
10. Outscraper Maps scraping: https://outscraper.com/google-maps-scraper/

## Official Google APIs and policies

11. Aggregate API overview: https://developers.google.com/maps/documentation/places-aggregate/overview
12. Aggregate request parameters (100 IDs, geometry constraints): https://developers.google.com/maps/documentation/places-aggregate/request-parameters
13. Aggregate request examples (`computeInsights`, REST and Python): https://developers.google.com/maps/documentation/places-aggregate/example-requests
14. Aggregate usage/billing: https://developers.google.com/maps/documentation/places-aggregate/usage-and-billing
15. Nearby Search (New) (20 max): https://developers.google.com/maps/documentation/places/web-service/nearby-search
16. Text Search (New), field mask/pagination: https://developers.google.com/maps/documentation/places/web-service/text-search
17. Place Details (New): https://developers.google.com/maps/documentation/places/web-service/place-details
18. Place data fields/SKU mapping: https://developers.google.com/maps/documentation/places/web-service/data-fields
19. Google Maps Platform pricing list: https://developers.google.com/maps/billing-and-pricing/pricing
20. Google Places policies and caching: https://developers.google.com/maps/documentation/places/web-service/policies
21. Google Aggregate API policies: https://developers.google.com/maps/documentation/places-aggregate/policies
22. Google Maps Platform service-specific terms: https://cloud.google.com/maps-platform/terms/maps-service-terms
23. Google Place IDs and caching exception: https://developers.google.com/maps/documentation/places/web-service/place-id
24. Places Insights (optional counts, trends and H3): https://developers.google.com/maps/documentation/placesinsights/overview
25. Places Insights release notes: https://developers.google.com/maps/documentation/placesinsights/release-notes

## Open-source collectors and independent reference data

26. gosom Google Maps scraper, MIT, grid mode: https://github.com/gosom/google-maps-scraper
27. omkarcloud scraper discussion about coordinate/zoom coverage: https://github.com/omkarcloud/google-maps-scraper/discussions/132
28. Overture Maps Places data and caveats: https://docs.overturemaps.org/guides/places/
29. Overture source attribution/licensing: https://docs.overturemaps.org/attribution/
30. Overture Python CLI + bbox: https://docs.overturemaps.org/getting-data/overturemaps-py/
31. Overture data repo: https://github.com/OvertureMaps/data
32. PostGIS: https://postgis.net/documentation/
33. Shapely manual: https://shapely.readthedocs.io/
34. Apify actor/runner docs: https://docs.apify.com/platform/actors

## Confirmed facts vs architecture hypotheses

**Confirmed from linked official/provider documentation:**

- Google Aggregate supports count and ID retrieval only at counts <=100; minimum and maximum area, orientation and vertex limits apply.
- Nearby (New) max 20 per request; Text Search (New) allows pagination of 20 results/page up to its search maximum.
- Apify Compass supports geographic targeting/auto partitioning and charges separate feature events.
- gosom supports grid bbox/cell/zoom with an explicit warning that results are not strictly clipped.
- Overture's places theme has tens of millions of records and explicitly acknowledges duplicates/junk and incompleteness.
- Official Google API data has special retention and display restrictions; Place ID is a documented exception.

**Working hypotheses to benchmark, not provider guarantees:**

- Any particular zoom/cell size universally gives near-complete listing recall.
- Adaptive offset probes recover most records hidden by ranked results.
- Parent Aggregate API count equals the exact union of child source IDs for arbitrary polygon splits.
- Apify native grid and gosom grid expose identical POI universes.
- External reference datasets can stand in as exact Google Maps ground truth.

The purpose of the build and QA plan is to test these hypotheses rather than silently encode them as facts.
