---
title: Ocea Collector
description: Monitor Ocea water and CETC consumption in Home Assistant.
ha_category:
  - Energy
  - Water
ha_iot_class: Cloud Polling
ha_domain: ocea_collector
ha_platforms:
  - sensor
ha_config_flow: true
ha_codeowners:
  - "@swappers"
---

Monitor your Ocea consumption (cold/hot water and CETC) and expose totals for the
Energy and Water dashboards.

## Prerequisites

- An Ocea account with access to the resident portal.
- This integration uses Ocea's web API (no official public API is available).

## Configuration

Add the integration from the UI:

1. Settings > Devices & Services > Add Integration > Ocea Collector
2. Enter your Ocea username and password.
3. Choose an update interval (1h / 3h / 6h).

## Sensors

Created per fluid (eau froide, eau chaude, CETC):

- **Total** (L or kWh): `total_increasing`, recommended for dashboards.
- **Daily** (L or kWh): derived/estimated daily consumption.

Water only:

- **Leak Estimate** (raw): `fuiteEstimee` from the API (string as-is).

Useful attributes:

- `latest_date`: effective date used by the integration.
- `api_latest_date`: raw date returned by Ocea.
- `daily_status` / `value_status`: data quality flags.

## Energy / Water dashboards

- Energy dashboard: **Ocea CETC Total** (kWh).
- Water dashboard: **Ocea Eau froide Total** / **Ocea Eau chaude Total** (L).

Daily sensors are informative only; totals are used to build statistics.

## Notes about dates (D-1 / D-2)

Ocea often updates once in the morning. Before that update, values may still
reflect D-2. When the API returns the first day of the month as `latest_date`,
the integration uses yesterday as the effective date.

## Troubleshooting

If the integration cannot authenticate, verify your credentials and retry. If
values remain stale for several days, check Ocea's portal status.
