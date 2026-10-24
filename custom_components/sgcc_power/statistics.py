"""历史日电量/日电费回填 HA 长期统计（能源面板立即可用完整历史）。

设计要点：
- 每天一条：sum = 截至当天末的累计值，state = 当天值。能源面板按 sum 差分
  计算每日消耗，首日不会出现「从 0 跳到总额」的假尖峰。
- 游标（last_imported_day / last_imported_total）持久化在
  ``.storage/sgcc_power.statistics``，只导入新数据，导入失败不推进游标。
- 基线自校验分两种情况（与 state_grid_info 的固定账本不同，本集成的 readings
  只有 2-3 个月的查询窗口，会随时间滑动）：
    * 游标日仍在窗口内：用当前序列重算累计值比对游标，不一致 → 全量重导；
    * 游标日已滑出窗口：无法重算，信任游标累计值继续追加（sum 不回退）。
- 只导入距今至少 _STABILITY_DAYS 天的记录，避免次日修正被固化。
- statistic_id 为外部统计格式：sgcc_power:total_energy_<户号>（kWh）、
  sgcc_power:total_cost_<户号>（元，仅上游提供日电费的地区会产生数据）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from .models import AccountUsage

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_STABILITY_DAYS = 2
_BASELINE_TOLERANCE = 0.01

_STAT_KEY_ENERGY = "total_energy"
_STAT_KEY_COST = "total_cost"


def energy_statistic_id(account_number: str) -> str:
    return f"sgcc_power:total_energy_{account_number}"


def cost_statistic_id(account_number: str) -> str:
    return f"sgcc_power:total_cost_{account_number}"


def clean_series(readings: Iterable[Any], field: str) -> list[tuple[str, float]]:
    """把日记录规范成按日期升序的 [(day_iso, value)]，跳过非法/零值。"""
    series: list[tuple[str, float]] = []
    for reading in readings:
        day = getattr(reading, "day", None)
        value = getattr(reading, field, None)
        if day is None or value is None or value <= 0:
            continue
        series.append((day.isoformat(), float(value)))
    series.sort(key=lambda item: item[0])
    return series


def baseline_outdated(
    series: list[tuple[str, float]],
    *,
    last_imported_day: str | None,
    expected_total: float,
) -> bool | None:
    """判断游标基线是否失效。

    返回 True 表示需要全量重导（游标日仍在窗口内但累计对不上）；
    False 表示基线正常；None 表示游标日已滑出窗口、无法重算（信任游标）。
    """
    if last_imported_day is None:
        return False
    if not series or series[0][0] > last_imported_day:
        return None
    total = 0.0
    for day_str, value in series:
        if day_str > last_imported_day:
            break
        total = round(total + value, 4)
    return abs(total - expected_total) > _BASELINE_TOLERANCE


def build_daily_stats(
    series: list[tuple[str, float]],
    *,
    last_imported_day: str | None,
    running_total: float,
    stability_days: int = _STABILITY_DAYS,
    today: date | None = None,
) -> tuple[list[dict[str, Any]], str | None, float]:
    """把序列转成统计记录（纯函数：start 为当天 00:00 的 naive datetime）。

    返回 (records, new_last_day, new_total)。record 形如
    ``{"day": date, "sum": float, "state": float}``，由调用方转成 StatisticData。
    """
    today = today or date.today()
    cutoff = (today - timedelta(days=stability_days)).isoformat()

    records: list[dict[str, Any]] = []
    new_last_day = last_imported_day
    new_total = running_total
    for day_str, value in series:
        if last_imported_day is not None and day_str <= last_imported_day:
            continue
        if day_str > cutoff:
            continue
        running_total = round(running_total + value, 4)
        records.append(
            {
                "day": date.fromisoformat(day_str),
                "sum": running_total,
                "state": round(value, 4),
            }
        )
        new_last_day = day_str
        new_total = running_total
    return records, new_last_day, new_total


class StatisticsImporter:
    """持久化游标并把刷新结果回填到 HA 长期统计。"""

    STORE_KEY = "sgcc_power.statistics"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._cursors: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        from homeassistant.helpers.storage import Store

        store = Store(self._hass, 1, self.STORE_KEY)
        data = await store.async_load()
        self._cursors = dict(data) if isinstance(data, dict) else {}

    async def _async_save_cursors(self) -> None:
        from homeassistant.helpers.storage import Store

        store = Store(self._hass, 1, self.STORE_KEY)
        await store.async_save(self._cursors)

    def _cursor(self, account_id: str, stat_key: str) -> dict[str, Any]:
        entry = self._cursors.setdefault(account_id, {})
        cursor = entry.setdefault(stat_key, {})
        return {
            "last_imported_day": cursor.get("last_imported_day") or None,
            "last_imported_total": float(cursor.get("last_imported_total", 0.0) or 0.0),
        }

    def _set_cursor(
        self, account_id: str, stat_key: str, last_day: str, total: float
    ) -> None:
        self._cursors.setdefault(account_id, {})[stat_key] = {
            "last_imported_day": last_day,
            "last_imported_total": total,
        }

    async def async_import(self, usages: dict[str, AccountUsage]) -> None:
        """刷新成功后调用；单户号失败不影响其他户号与主数据链路。"""
        for usage in usages.values():
            account_number = usage.account.cons_no_src or usage.account.cons_no
            if not account_number:
                continue
            try:
                await self._async_import_series(
                    usage,
                    stat_key=_STAT_KEY_ENERGY,
                    stat_id=energy_statistic_id(account_number),
                    name=f"国家电网 累计用电 {account_number[-4:]}",
                    field="usage",
                    unit="kWh",
                    unit_class="energy",
                )
                await self._async_import_series(
                    usage,
                    stat_key=_STAT_KEY_COST,
                    stat_id=cost_statistic_id(account_number),
                    name=f"国家电网 累计电费 {account_number[-4:]}",
                    field="charge",
                    unit="CNY",
                    unit_class=None,
                )
            except Exception:  # noqa: BLE001 - 统计是附加能力，失败不影响实体
                _LOGGER.warning(
                    "统计回填失败（户号尾号 %s）", account_number[-4:], exc_info=True
                )
        await self._async_save_cursors()

    async def _async_import_series(
        self,
        usage: AccountUsage,
        *,
        stat_key: str,
        stat_id: str,
        name: str,
        field: str,
        unit: str,
        unit_class: str | None,
    ) -> None:
        from datetime import datetime

        from homeassistant.components.recorder.models import (
            StatisticData,
            StatisticMeanType,
            StatisticMetaData,
        )
        from homeassistant.components.recorder.statistics import (
            async_add_external_statistics,
        )
        from homeassistant.util import dt as dt_util

        account_id = usage.account.account_id
        cursor = self._cursor(account_id, stat_key)
        last_day = cursor["last_imported_day"]
        total = cursor["last_imported_total"]

        series = clean_series(usage.readings, field)
        outdated = baseline_outdated(
            series, last_imported_day=last_day, expected_total=total
        )
        if outdated is True:
            _LOGGER.info("%s 历史数据被上游修订，统计将全量重导", stat_id)
            last_day, total = None, 0.0
        # outdated is None（游标滑出窗口）→ 信任游标累计值继续追加

        records, new_last_day, new_total = build_daily_stats(
            series, last_imported_day=last_day, running_total=total
        )
        if not records:
            return

        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_mean=False,
            has_sum=True,
            name=name,
            source="sgcc_power",
            statistic_id=stat_id,
            unit_class=unit_class,
            unit_of_measurement=unit,
        )
        stats = [
            StatisticData(
                start=datetime(
                    record["day"].year,
                    record["day"].month,
                    record["day"].day,
                    tzinfo=dt_util.DEFAULT_TIME_ZONE,
                ),
                sum=record["sum"],
                state=record["state"],
            )
            for record in records
        ]
        async_add_external_statistics(self._hass, metadata, stats)
        self._set_cursor(account_id, stat_key, new_last_day, new_total)
        _LOGGER.info(
            "已回填 %d 条 %s 统计（最新日=%s，累计=%.2f %s）",
            len(stats),
            stat_id,
            new_last_day,
            new_total,
            unit,
        )
