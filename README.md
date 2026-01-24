# Ocea Collector

[![GitHub Release](https://img.shields.io/github/v/release/swappers/ocea-collector-integration?sort=semver)](https://github.com/swappers/ocea-collector-integration/releases)
[![Commits since release](https://img.shields.io/github/commits-since/swappers/ocea-collector-integration/latest)](https://github.com/swappers/ocea-collector-integration/commits/main)
[![Last Commit](https://img.shields.io/github/last-commit/swappers/ocea-collector-integration)](https://github.com/swappers/ocea-collector-integration/commits/main)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)

[![Add integration to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=ocea_collector)

GitHub: https://github.com/swappers/ocea-collector-integration

<img src="custom_components/ocea_collector/icon.png" alt="Ocea" height="56" />

Unofficial integration of Ocea consumption (water + CETC) for Home Assistant. Adds totals for Energy/Water
dashboards and daily estimates.

## Quick start

1) HACS: add this repo and install the integration.
2) Restart Home Assistant.
3) Settings > Devices & Services > Add Integration > Ocea Collector.
4) Enter Ocea username, password, and update interval (1h / 3h / 6h).


## Sensors

Created per fluid (eau froide, eau chaude, CETC):
- Total (L or kWh) – `total_increasing` for dashboards.
- Daily (L or kWh) – derived/estimated daily consumption.

Water only:
- Leak Estimate (raw) – `fuiteEstimee` from the API (string as-is).

Useful attributes: `latest_date`, `api_latest_date`, `daily_status`, `value_status`.

## Energy / Water dashboards

- Energy dashboard: use **Ocea CETC Total** (kWh).
- Water dashboard: use **Ocea Eau froide Total** / **Ocea Eau chaude Total** (L).

Daily sensors are informative only; totals are used to build statistics.

## Notes about dates (D-1)

Ocea often updates once in the morning. Before that, values can still reflect D-2.
If the API returns a "first day of month" date, the integration uses yesterday
as the effective date. The raw API date is exposed as `api_latest_date`.
