"""Compatibility writer for legacy state_grid / state_grid_info consumers.

旧版 state_grid 集成把每个户号的数据以固定结构写入
``.storage/state_grid.config``，xiaoshi930/state_grid_info 等辅助集成直接读取
该文件。本模块在新集成成功刷新后，把按新 API 取到的数据转换成同构结构
写回同一文件，使这类辅助集成在旧登录链路失效后继续有数据可用。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .const import LEGACY_STORAGE_KEY, LEGACY_STORAGE_VERSION
from .models import AccountUsage

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _day_entry(reading: Any) -> dict[str, Any]:
    """一条日记录，字段名沿用旧版 daily_bill_list 的命名。"""
    return {
        "day": reading.day.strftime("%Y%m%d"),
        "dayElePq": reading.usage,
        "thisTPq": reading.tip,
        "thisPPq": reading.peak,
        "thisNPq": reading.flat,
        "thisVPq": reading.valley,
    }


def build_legacy_power_user(usage: AccountUsage) -> dict[str, Any]:
    """把一个户号的 AccountUsage 转成旧版 powerUserList 元素。"""
    account = usage.account
    daily = [_day_entry(reading) for reading in usage.readings]
    latest = usage.latest
    month_bills: list[dict[str, Any]] = []
    for bill in usage.monthly_bills:
        month_readings = [
            reading
            for reading in usage.readings
            if reading.day.year == bill.month.year
            and reading.day.month == bill.month.month
        ]
        month_bills.append(
            {
                "month": bill.month.strftime("%Y%m"),
                "monthEleNum": bill.usage,
                "monthEleCost": bill.charge,
                "month_t_ele_num": sum(reading.tip or 0 for reading in month_readings),
                "month_p_ele_num": sum(reading.peak or 0 for reading in month_readings),
                "month_n_ele_num": sum(reading.flat or 0 for reading in month_readings),
                "month_v_ele_num": sum(
                    reading.valley or 0 for reading in month_readings
                ),
                "daily_ele": [_day_entry(reading) for reading in month_readings],
            }
        )

    balance_value = (
        usage.billing_account.balance if usage.billing_account is not None else None
    )
    return {
        "consNo_dst": account.cons_no_src or account.cons_no,
        "consName_dst": account.name,
        "elecAddr_dst": account.address,
        "balance": balance_value,
        "refresh_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "daily_bill_list": daily,
        "month_bill_list": month_bills,
        "daily_ele_num": latest.usage if latest else None,
        "daily_p_ele_num": latest.peak if latest else None,
        "daily_v_ele_num": latest.valley if latest else None,
        "daily_n_ele_num": latest.flat if latest else None,
        "daily_t_ele_num": latest.tip if latest else None,
        "daily_lasted_date": latest.day.isoformat() if latest else None,
        "month_ele_num": usage.month_sum("usage"),
        "month_p_ele_num": usage.month_sum("peak"),
        "month_v_ele_num": usage.month_sum("valley"),
        "month_n_ele_num": usage.month_sum("flat"),
        "month_t_ele_num": usage.month_sum("tip"),
        "year_ele_num": usage.current_year_usage,
        "year_ele_cost": usage.current_year_charge,
        "last_month_ele_num": usage.latest_bill.usage if usage.latest_bill else None,
        "last_month_ele_cost": (
            usage.latest_bill.charge if usage.latest_bill else None
        ),
        "last_month_meter_num": (
            usage.latest_month_meter.reading if usage.latest_month_meter else 0
        ),
    }


class LegacyStoreWriter:
    """把刷新结果写入旧版 state_grid.config 存储文件。"""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def async_write(self, usages: dict[str, AccountUsage]) -> None:
        """合并写入；已有文件中未涉及的户号原样保留。"""
        try:
            from homeassistant.helpers.storage import Store

            store = Store(
                self._hass,
                LEGACY_STORAGE_VERSION,
                LEGACY_STORAGE_KEY,
                atomic_writes=True,
            )
            existing = await store.async_load() or {}
            power_users: dict[str, dict[str, Any]] = {
                str(user.get("consNo_dst") or idx): dict(user)
                for idx, user in enumerate(existing.get("powerUserList") or [])
                if isinstance(user, dict)
            }
            for usage in usages.values():
                power_users[usage.account.cons_no_src or usage.account.cons_no] = (
                    build_legacy_power_user(usage)
                )
            await store.async_save({"powerUserList": list(power_users.values())})
        except Exception:  # noqa: BLE001 - 兼容层失败不应影响主数据链路
            _LOGGER.warning(
                "无法写入旧版兼容存储文件 %s，辅助集成可能得不到新数据",
                LEGACY_STORAGE_KEY,
                exc_info=True,
            )
