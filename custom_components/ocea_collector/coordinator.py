"""Ocea Collector update coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
import random
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    StatisticMeanType,
    StatisticsRow,
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter, VolumeConverter

from .const import (
    ATTRIBUTION,
    AUTH_RETRY_DELAY_SECONDS,
    AUTH_RETRY_MAX,
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    UPDATE_INTERVAL_JITTER_SECONDS,
)
from .ocea_client import FLUIDS, OceaAuthError, OceaClient

_LOGGER = logging.getLogger(__name__)


@dataclass
class FluidData:
    """Store data for one fluid."""

    total: float | None
    unit: str | None
    leak_estimate: str | None
    daily: float | None
    daily_status: str
    daily_source: str | None
    estimated_today: float | None
    estimated_today_source: str | None
    latest_date: str | None
    api_latest_date: str | None
    value_status: str
    last_total: float | None
    last_total_at: str | None


@dataclass
class OceaData:
    """Aggregated ocea data."""

    fluids: dict[str, FluidData]


OceaConfigEntry = ConfigEntry


class OceaCoordinator(DataUpdateCoordinator[OceaData]):
    """Coordinator for Ocea Collector."""

    def __init__(self, hass: HomeAssistant, entry: OceaConfigEntry) -> None:
        base_interval = entry.options.get(
            CONF_UPDATE_INTERVAL,
            entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_SCAN_INTERVAL.seconds),
        )
        jitter = random.randint(-UPDATE_INTERVAL_JITTER_SECONDS, UPDATE_INTERVAL_JITTER_SECONDS)
        update_interval_seconds = max(60, base_interval + jitter)
        update_interval = timedelta(seconds=update_interval_seconds)
        _LOGGER.info(
            "Ocea update interval: base=%ss jitter=%ss effective=%ss",
            base_interval,
            jitter,
            update_interval_seconds,
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
            always_update=True,
            config_entry=entry,
        )
        self._entry = entry
        self._client: OceaClient | None = None
        self._store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}")
        self._store_data: dict[str, Any] | None = None
        self._auth_retry_count = 0

    async def _async_setup(self) -> None:
        """Initialize client."""
        self._client = OceaClient(
            username=self._entry.data[CONF_USERNAME],
            password=self._entry.data[CONF_PASSWORD],
        )
        await self._ensure_store_loaded()

    async def _ensure_store_loaded(self) -> None:
        if self._store_data is None:
            self._store_data = await self._store.async_load() or {"fluids": {}}

    async def _async_update_data(self) -> OceaData:
        if self._client is None:
            await self._async_setup()

        try:
            raw = await self.hass.async_add_executor_job(self._client.fetch)
        except OceaAuthError as err:
            message = str(err)
            if "HTTP 401" in message:
                if self._auth_retry_count < AUTH_RETRY_MAX:
                    self._auth_retry_count += 1
                    _LOGGER.warning(
                        "Auth error (%s). Retrying in %ss (%s/%s).",
                        message,
                        AUTH_RETRY_DELAY_SECONDS,
                        self._auth_retry_count,
                        AUTH_RETRY_MAX,
                    )
                    raise UpdateFailed(
                        message, retry_after=AUTH_RETRY_DELAY_SECONDS
                    ) from err
                _LOGGER.error(
                    "Auth error retry limit reached (%s/%s); waiting for next cycle.",
                    self._auth_retry_count,
                    AUTH_RETRY_MAX,
                )
            else:
                self._auth_retry_count = 0
            raise UpdateFailed(message) from err
        except Exception as err:
            raise UpdateFailed("Ocea coordinator error communicating with API") from err

        self._auth_retry_count = 0

        await self._ensure_store_loaded()

        now = dt_util.now()
        _LOGGER.info("Ocea fetch completed at %s", now.isoformat())

        fluids: dict[str, FluidData] = {}
        store_fluids = self._store_data.setdefault("fluids", {})

        for key, meta in FLUIDS.items():
            unit = meta.get("unit")
            raw_entry = raw.get(key, {})
            current_total = raw_entry.get("latest_value")
            leak_estimate = raw_entry.get("leak_estimate")
            if leak_estimate is None:
                leak_estimate = "unknown"
            api_date = _parse_date(raw_entry.get("latest_date"))
            current_date = api_date
            if current_date is None or (
                current_date.day == 1 and now.date().day > 1
            ):
                current_date = now.date() - timedelta(days=1)

            fluid_store = store_fluids.get(key, {})
            last_total = fluid_store.get("last_total")
            last_total_at = _parse_date(fluid_store.get("last_total_at"))

            daily_value = None
            daily_status = "unknown"
            daily_source = None
            estimated_today = None
            estimated_source = None
            value_status = "unknown"

            value_used = current_total
            if current_total is None or current_date is None:
                value_status = "missing"
            elif current_total < 0:
                value_status = "invalid"
            elif last_total is not None and last_total_at is not None:
                if current_date < last_total_at:
                    value_status = "invalid"
                elif current_date.month == last_total_at.month:
                    if current_total == 0 and last_total > 0:
                        value_status = "invalid"
                    elif current_total + 1e-6 < last_total:
                        value_status = "invalid"

            if value_status == "unknown":
                value_status = "ok"

            if (
                value_status == "ok"
                and current_total is not None
                and last_total is not None
                and current_date is not None
                and last_total_at is not None
                and current_total == last_total
                and current_date > last_total_at
            ):
                value_status = "stale"

            if value_status in ("missing", "invalid"):
                if last_total is not None:
                    value_used = last_total
                    value_status = "stale"
                else:
                    value_used = None

            if value_status == "stale":
                daily_status = "stale"
                daily_source = "stale_total"
            elif value_status != "ok" and value_used is None:
                daily_status = "missing"
            elif value_status != "ok":
                daily_status = "invalid"
                daily_source = "invalid_total"
            elif last_total is None or last_total_at is None or current_date is None:
                daily_status = "missing"
            else:
                if current_date == last_total_at:
                    delta = current_total - last_total
                    if delta < 0:
                        daily_status = "invalid"
                        daily_source = "negative_delta"
                    elif delta == 0:
                        daily_status = "stale"
                    else:
                        daily_status = "corrected"
                        daily_source = "same_day_correction"
                        try:
                            corrected = await self._update_statistics_correction(
                                key,
                                unit,
                                current_date,
                                delta,
                            )
                            if corrected is not None:
                                daily_value = corrected
                        except Exception as err:
                            _LOGGER.debug(
                                "Failed to update correction statistics: %s",
                                err,
                                exc_info=True,
                            )
                else:
                    stats_start = last_total_at
                    delta = current_total - last_total
                    if delta < 0 and last_total_at.month != current_date.month:
                        delta = current_total
                        daily_source = "month_reset"
                        month_start = current_date.replace(day=1)
                        stats_start = month_start - timedelta(days=1)
                    days_between = (current_date - stats_start).days
                    if days_between >= 1 and delta == 0:
                        daily_status = "stale"
                        daily_source = "no_change"
                    elif days_between >= 1 and delta >= 0:
                        daily_value = round(delta / days_between, 3)
                        daily_status = "ok" if days_between == 1 else "estimated"
                        daily_source = daily_source or (
                            "delta" if days_between == 1 else "multi_day_estimate"
                        )
                        try:
                            await self._update_statistics_range(
                                key,
                                unit,
                                stats_start,
                                current_date,
                                daily_value,
                            )
                        except Exception as err:
                            _LOGGER.debug(
                                "Failed to update statistics: %s", err, exc_info=True
                            )
                    elif days_between >= 1 and delta < 0:
                        daily_status = "invalid"
                        daily_source = "negative_delta"

            if daily_value is not None:
                estimated_today = daily_value
                estimated_source = daily_source
            elif value_used is not None:
                average_date = current_date or last_total_at or now.date()
                estimated_today = round(value_used / max(average_date.day, 1), 3)
                estimated_source = "monthly_average"

            if value_status == "ok" and current_total is not None and current_date is not None:
                if last_total is None or last_total_at is None or current_total != last_total:
                    last_total = current_total
                    last_total_at = current_date
                    fluid_store["last_total"] = current_total
                    fluid_store["last_total_at"] = current_date.isoformat()
            elif last_total_at is not None and last_total is not None:
                fluid_store["last_total"] = last_total
                fluid_store["last_total_at"] = last_total_at.isoformat()

            store_fluids[key] = fluid_store

            fluids[key] = FluidData(
                total=value_used,
                unit=unit,
                leak_estimate=leak_estimate,
                daily=daily_value,
                daily_status=daily_status,
                daily_source=daily_source,
                estimated_today=estimated_today,
                estimated_today_source=estimated_source,
                latest_date=current_date.isoformat() if current_date else None,
                api_latest_date=api_date.isoformat() if api_date else None,
                value_status=value_status,
                last_total=last_total,
                last_total_at=last_total_at.isoformat() if last_total_at else None,
            )

            label = meta.get("label", key)
            _LOGGER.info(
                "Ocea %s at %s: total=%s %s leak=%s api_date=%s effective_date=%s status=%s daily=%s daily_status=%s",
                label,
                now.isoformat(),
                value_used,
                unit or "",
                leak_estimate,
                api_date.isoformat() if api_date else None,
                current_date.isoformat() if current_date else None,
                value_status,
                daily_value,
                daily_status,
            )

        await self._store.async_save(self._store_data)
        return OceaData(fluids=fluids)

    async def _update_statistics_range(
        self,
        fluid_key: str,
        unit: str | None,
        start_date: date,
        end_date: date,
        per_day: float,
    ) -> None:
        """Update daily statistics, backfilling gaps with estimates."""
        stat_id = f"{DOMAIN}:{self._entry.entry_id}_{fluid_key}"
        last_stat = await self._get_last_stat(stat_id)
        sum_value = last_stat["sum"] if last_stat and last_stat.get("sum") else 0.0
        last_stat_date = (
            datetime.fromtimestamp(last_stat["start"]).date() if last_stat else None
        )

        if per_day < 0:
            return

        stats: list[StatisticData] = []
        stats_start = start_date
        if last_stat_date and last_stat_date > stats_start:
            stats_start = last_stat_date

        days_between = (end_date - stats_start).days
        if days_between <= 0:
            return

        for offset in range(1, days_between + 1):
            day = stats_start + timedelta(days=offset)
            sum_value += per_day
            stats.append(
                StatisticData(
                    start=dt_util.start_of_local_day(day),
                    state=per_day,
                    sum=sum_value,
                )
            )

        if not stats:
            return

        metadata = self._get_statistics_metadata(stat_id, f"{fluid_key} consumption", unit)
        if metadata is None:
            return
        async_add_external_statistics(self.hass, metadata, stats)

    async def _update_statistics_correction(
        self,
        fluid_key: str,
        unit: str | None,
        day: date,
        delta: float,
    ) -> float | None:
        """Correct the most recent day when the API updates same-day values."""
        if delta <= 0:
            return None

        stat_id = f"{DOMAIN}:{self._entry.entry_id}_{fluid_key}"
        last_stat = await self._get_last_stat(stat_id)
        if not last_stat:
            return None

        last_stat_date = datetime.fromtimestamp(last_stat["start"]).date()
        if last_stat_date != day:
            return None

        last_state = last_stat.get("state") or 0.0
        last_sum = last_stat.get("sum") or 0.0
        new_state = round(last_state + delta, 3)
        if new_state < 0:
            return None
        new_sum = last_sum - last_state + new_state

        metadata = self._get_statistics_metadata(stat_id, f"{fluid_key} consumption", unit)
        if metadata is None:
            return None

        stats = [
            StatisticData(
                start=dt_util.start_of_local_day(day),
                state=new_state,
                sum=new_sum,
            )
        ]
        async_add_external_statistics(self.hass, metadata, stats)
        return new_state

    def _get_statistics_metadata(
        self, stat_id: str, name: str, unit: str | None
    ) -> StatisticMetaData | None:
        if unit == "m3" or unit == "L":
            unit_class = VolumeConverter.UNIT_CLASS
            unit_name = UnitOfVolume.LITERS if unit == "L" else UnitOfVolume.CUBIC_METERS
        elif unit == "kWh":
            unit_class = EnergyConverter.UNIT_CLASS
            unit_name = UnitOfEnergy.KILO_WATT_HOUR
        else:
            return None

        return StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"Ocea {name}",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class=unit_class,
            unit_of_measurement=unit_name,
        )

    async def _get_last_stat(self, stat_id: str) -> StatisticsRow | None:
        last_stat = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, stat_id, True, {"sum", "state"}
        )
        return last_stat[stat_id][0] if last_stat and stat_id in last_stat else None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        parsed = dt_util.parse_datetime(value)
        if parsed:
            return parsed.date()
        return date.fromisoformat(value)
    except ValueError:
        return None
