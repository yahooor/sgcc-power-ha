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


def cost_series(
    readings: Iterable[Any], price: float, *, tiered: bool = False
) -> list[tuple[str, float]]:
    """构造日电费序列：优先上游日电费（charge），缺失时估算。

    固定单价模式：用量 × price。阶梯模式（湖南居民分时外季节阶梯）：
    按自然月累计电量分段——春秋月（3/4/5/9/10/11）二档 200、三档 350 kWh，
    其余月份二档 200、三档 450；档位加价 +0.05 / +0.30 元（基于 base=price）。
    price <= 0 表示不生成成本统计，返回空列表。
    """
    if price <= 0:
        return []
    if not tiered:
        series = []
        for reading in readings:
            day = getattr(reading, "day", None)
            if day is None:
                continue
            charge = getattr(reading, "charge", None)
            if charge is not None and charge > 0:
                series.append((day.isoformat(), float(charge)))
                continue
            usage = getattr(reading, "usage", None)
            if usage is not None and usage > 0:
                series.append((day.isoformat(), round(float(usage) * price, 4)))
        series.sort(key=lambda item: item[0])
        return series
    return tiered_cost_series(readings, price)


_SPRING_AUTUMN_MONTHS = {3, 4, 5, 9, 10, 11}
_TIER2_USAGE = 200.0
_TIER3_SPRING_AUTUMN = 350.0
_TIER3_WINTER_SUMMER = 450.0
_TIER2_ADD = 0.05
_TIER3_ADD = 0.30


def tiered_cost_series(
    readings: Iterable[Any], base_price: float
) -> list[tuple[str, float]]:
    """湖南居民季节阶梯：按自然月累计电量分段计价（纯函数）。"""
    items: list[tuple[str, float]] = []
    for reading in readings:
        day = getattr(reading, "day", None)
        if day is None:
            continue
        charge = getattr(reading, "charge", None)
        usage = getattr(reading, "usage", None)
        value = usage if usage is not None and usage > 0 else None
        if value is None:
            continue
        if charge is not None and charge > 0:
            items.append((day.isoformat(), float(charge), float(value), True))
        else:
            items.append((day.isoformat(), float(value), float(value), False))
    items.sort(key=lambda item: item[0])

    month_accumulated: dict[str, float] = {}
    series: list[tuple[str, float]] = []
    for day_iso, primary, usage_value, is_real_charge in items:
        month_key = day_iso[:7]
        prior = month_accumulated.get(month_key, 0.0)
        if is_real_charge:
            cost = primary
        else:
            tier3_start = (
                _TIER3_SPRING_AUTUMN
                if int(month_key[5:7]) in _SPRING_AUTUMN_MONTHS
                else _TIER3_WINTER_SUMMER
            )
            cost = _tier_cost(usage_value, prior, _TIER2_USAGE, tier3_start, base_price)
        month_accumulated[month_key] = round(prior + usage_value, 4)
        series.append((day_iso, round(cost, 4)))
    return series


def _tier_cost(
    day_usage: float,
    prior_usage: float,
    tier2_start: float,
    tier3_start: float,
    base_price: float,
) -> float:
    """把一天用量按当月已累计电量切进三档，返回电费。"""
    remaining = day_usage
    cost = 0.0
    boundaries = (
        (0.0, tier2_start, base_price),
        (tier2_start, tier3_start, base_price + _TIER2_ADD),
        (tier3_start, float("inf"), base_price + _TIER3_ADD),
    )
    for start, end, unit_price in boundaries:
        if remaining <= 0:
            break
        # 本档可用额度：从 max(prior, start) 到 end
        available = max(0.0, end - max(prior_usage, start))
        segment = min(remaining, available)
        if segment > 0:
            cost += segment * unit_price
            remaining -= segment
    # prior 已超最高档或边界容差兜底
    if remaining > 0:
        cost += remaining * (base_price + _TIER3_ADD)
    return cost


def estimated_month_cost(
    readings: Iterable[Any],
    price: float,
    tiered: bool = False,
    *,
    today: date | None = None,
) -> float | None:
    """当月日电费合计（估算）：cost_series 限定当月。

    上游不返回 estiAmt 的地区，用本函数按阶梯/固定单价估算本月电费。
    price <= 0 或当月无数据返回 None。
    """
    if price <= 0:
        return None
    today = today or date.today()
    month_prefix = today.strftime("%Y-%m")
    values = [
        value
        for day, value in cost_series(readings, price, tiered=tiered)
        if day.startswith(month_prefix)
    ]
    if not values:
        return None
    return round(sum(values), 2)


def price_changed(stored: float | None, current: float) -> bool:
    """电价是否发生过变更（首次记录不算变更）。"""
    return stored is not None and stored != current


class StatisticsImporter:
    """持久化游标并把刷新结果回填到 HA 长期统计。"""

    STORE_KEY = "sgcc_power.statistics"
    _PRICE_KEY = "_price"
    _TIERED_KEY = "_tiered"

    def __init__(
        self, hass: HomeAssistant, price: float = 0.0, *, tiered: bool = False
    ) -> None:
        self._hass = hass
        self.price = price
        self.tiered = tiered
        self._cursors: dict[str, dict[str, Any]] = {}
        self._stored_price: float | None = None
        self._stored_tiered: bool | None = None

    async def async_load(self) -> None:
        from homeassistant.helpers.storage import Store

        store = Store(self._hass, 1, self.STORE_KEY)
        data = await store.async_load()
        if isinstance(data, dict):
            self._cursors = dict(data)
            raw_price = data.get(self._PRICE_KEY)
            self._stored_price = float(raw_price) if raw_price is not None else None
            raw_tiered = data.get(self._TIERED_KEY)
            self._stored_tiered = bool(raw_tiered) if raw_tiered is not None else None
        else:
            self._cursors = {}
            self._stored_price = None
            self._stored_tiered = None

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

    def _reset_cost_cursors(self) -> None:
        """电价变更后清掉成本游标，让下一次导入全量重算。"""
        for value in self._cursors.values():
            if isinstance(value, dict):
                value.pop(_STAT_KEY_COST, None)

    def _async_remove_cost_statistics(self, account_number: str) -> None:
        """电价改为 0（关闭成本）时，把已导入的成本统计一并移除。"""
        from homeassistant.components.recorder.statistics import (
            async_remove_external_statistics,
        )

        async_remove_external_statistics(
            self._hass, "sgcc_power", cost_statistic_id(account_number)
        )

    async def async_import(self, usages: dict[str, AccountUsage]) -> None:
        """刷新成功后调用；单户号失败不影响其他户号与主数据链路。"""
        changed = price_changed(self._stored_price, self.price)
        tiered_toggled = (
            self._stored_tiered is not None and self._stored_tiered != self.tiered
        )
        cost_reset = changed or tiered_toggled
        if cost_reset:
            self._reset_cost_cursors()
            _LOGGER.info(
                "成本口径已变更（电价 %.4f→%.4f，阶梯 %s→%s），成本统计将整体重建",
                self._stored_price or 0.0,
                self.price,
                self._stored_tiered,
                self.tiered,
            )
        self._cursors[self._PRICE_KEY] = self.price
        self._cursors[self._TIERED_KEY] = self.tiered
        self._stored_price = self.price
        self._stored_tiered = self.tiered
        for usage in usages.values():
            account_number = usage.account.cons_no_src or usage.account.cons_no
            if not account_number:
                continue
            try:
                await self._async_import_series(
                    clean_series(usage.readings, "usage"),
                    account_id=usage.account.account_id,
                    stat_key=_STAT_KEY_ENERGY,
                    stat_id=energy_statistic_id(account_number),
                    name=f"国家电网 累计用电 {account_number[-4:]}",
                    unit="kWh",
                    unit_class="energy",
                )
                if self.price <= 0:
                    if cost_reset:
                        self._async_remove_cost_statistics(account_number)
                        _LOGGER.info(
                            "电价已设为 0，成本统计 %s 已移除",
                            cost_statistic_id(account_number),
                        )
                else:
                    if cost_reset:
                        # 全量重算只能覆盖当前查询窗口；先整体移除旧统计，
                        # 避免窗口外的旧行残留导致累计 sum 断崖（能源面板
                        # 跨界日会算出负成本）。
                        self._async_remove_cost_statistics(account_number)
                    await self._async_import_series(
                        cost_series(usage.readings, self.price, tiered=self.tiered),
                        account_id=usage.account.account_id,
                        stat_key=_STAT_KEY_COST,
                        stat_id=cost_statistic_id(account_number),
                        name=f"国家电网 累计电费 {account_number[-4:]}",
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
        series: list[tuple[str, float]],
        *,
        account_id: str,
        stat_key: str,
        stat_id: str,
        name: str,
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

        cursor = self._cursor(account_id, stat_key)
        last_day = cursor["last_imported_day"]
        total = cursor["last_imported_total"]

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
