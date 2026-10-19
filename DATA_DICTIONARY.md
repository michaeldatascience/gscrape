# GeoDiscovery - Canonical POI Data Dictionary and Source Metadata

This is the normalized data contract, not a claim that every provider exposes every field. A value can be `available`, `unavailable`, `not_requested`, `restricted`, `error`, or `not_applicable`. Record `provider`, `observed_at`, raw key and confidence for each field group.

## Record identity and metadata

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Internal durable ID. |
| `source_ids[]` | array | `{provider, source_id, source_kind, first_seen_at, last_seen_at}`. |
| `google_place_id` | string? | Stable for many uses but can change; stored only when actually supplied. |
| `google_cid`, `google_id`, `google_mid` | string? | Provider-specific, not universally available. |
| `maps_url` | URL? | Source listing URL. |
| `primary_source` | string | For provenance and display/retention policies. |
| `observed_at`, `first_seen_at`, `last_seen_at` | timestamp | First/last seen means our observations, not creation/opening dates. |
| `raw_observation_pointer` | string? | Only where source data may legally be retained. |
| `source_policy_id` | UUID | Controls retention/export/display. |
| `queried_categories[]`, `queried_keywords[]` | arrays | Preserve why business was found. |
| `matched_cells[]` | array | Every viewport/tile that returned a particular place. |

## Core place identity

| Field | Type | Normalization |
|---|---|---|
| `name`, `name_localized` | string/map? | Preserve source script and locale. |
| `primary_category`, `secondary_categories[]` | string/array | Provider taxonomy -> internal canonical category, preserve original. |
| `business_status` | enum | `operational`, `temporary_closed`, `permanent_closed`, `unknown`. |
| `opening_date` | date? | Only if explicitly returned; Google added this field to newer Places API. |
| `address_formatted`, `address_components` | string/object | Record original and normalized versions. |
| `unit`, `street`, `locality`, `city`, `borough`, `district`, `county`, `admin1`, `postal_code`, `country_code` | strings? | Some region levels overlap by country. |
| `latitude`, `longitude` | doubles? | WGS84, must not assume exact storefront location. |
| `plus_code`, `timezone`, `utc_offset_minutes` | string/int? | As available. |
| `business_inside_location[]` | array? | Nested businesses, malls, airports; optional. |
| `service_area_business`, `service_area` | bool/geometry? | Do not invent a storefront coordinate. |

## Standard business details

| Field | Type | Notes |
|---|---|---|
| `phone_national`, `phone_international`, `phones[]` | strings | Normalize with country context, keep raw. |
| `website_url`, `website_domain` | URL/string | Website may not be owned by business, preserve source. |
| `rating`, `rating_count` | number/int | Source-specific snapshot. |
| `price_level`, `price_range` | enum/object | Not available for many sectors. |
| `opening_hours_regular`, `opening_hours_current`, `secondary_hours` | structured maps | Timezone-aware; do not collapse to strings only. |
| `last_verified_at` | timestamp? | Only if actual provider supplies such a signal. |
| `listing_claimed` | bool? | Optional and source-specific; no official guarantee. |
| `reservation_links`, `ordering_links` | arrays | On request where offered. |

## Extended Maps place details (P2, cost-gated)

- Amenities and services: delivery, takeaway, dine-in, accessibility, pets, restrooms, parking, payment types, family-friendliness, restaurant service options, EV charging and related attributes.
- Popular times/busy times and live busyness if actually available; frequently omitted or inconsistent.
- Menus, order-online providers, booking or reservation integrations.
- Questions and answers, owner updates, place summaries, listed sub-destinations and "people also search" where available.
- Photo references, counts, categories and authors, if requested and permitted. Do not store images in MVP.
- Reviews: aggregate review count and rating in standard package; individual review content, timestamps, owner responses and reviewers only as explicit opt-in with provenance, additional charges and privacy policy.
- Hotel-specific fields, service-area information, business categories and prices when available.

## Future separate enrichment provider (not Maps collection)

These fields normally require visiting the company website or another business directory and must **not** be presented as native Google Maps fields:

- `emails[]`, social profiles, corporate phone numbers, HTML contact page links.
- Company ownership, staff names/roles, professional contacts.
- CMS/framework/marketing pixels, site speed, SEO issues, domain signals.
- Email verification, company financials, business age or revenue estimates.

The enrichment pipeline must have its own source, legal basis, crawl budget, freshness, retention and UI indicators.

## Filtering semantics

- `website_present=true`: only confirmed website URL provided (never infer absence from a skipped details fetch).
- `phone_present=true`: only if an actual source phone value exists.
- `primary_only=true`: matched category must be reported as main category by supported source; if unknown, flag uncertain, not true.
- `status=operational`: explicit source business status preferred. Missing status stays unknown.
- `in_territory=true`: requires coordinates and exact polygon validation; unresolved/service-area records are separate.
- `newly_discovered=true`: first seen by this platform within requested time window, not proven newly opened.

## Raw observation and field lineage format

```json
{
  "internal_place_id": "UUID",
  "source": "apify_compass",
  "source_place_id": "SOURCE_ID",
  "observed_at": "2026-10-17T00:00:00Z",
  "field_lineage": {
    "name": {"status": "available", "raw_key": "title", "source": "apify_compass"},
    "website_url": {"status": "not_requested", "source": "apify_compass"},
    "opening_date": {"status": "unavailable", "source": "apify_compass"}
  },
  "canonical": {
    "name": "Example Clinic",
    "website_url": null,
    "opening_date": null
  }
}
```

Schema is illustrative. It allows filtering missing data correctly and proves which records are backed by actual extracted source fields.
