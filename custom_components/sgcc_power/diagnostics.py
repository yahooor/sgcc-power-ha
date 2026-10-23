"""Diagnostics support for the 国家电网电力 integration.

只输出脱敏后的运行状态：不含手机号、密码、令牌、完整户号与地址。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from .const import CONF_AUTH_ERROR, DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import SgccPowerRuntimeData


def _masked(value: str | None) -> str:
    text = str(value or "")
    return "*" * max(0, len(text) - 4) + text[-4:] if len(text) > 4 else "****"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, with credentials redacted."""
    runtime: SgccPowerRuntimeData | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is None:
        return {"error": "integration not loaded"}

    api = runtime.api
    session = api.login_session

    accounts: list[dict[str, Any]] = []
    for account_id, usage in (runtime.coordinator.data or {}).items():
        account = usage.account
        accounts.append(
            {
                "id_suffix": _masked(account_id),
                "pro_no": account.pro_no,
                "org_no": account.org_no,
                "elec_type": account.elec_type,
                "reading_days": len(usage.readings),
                "latest_day": usage.latest.day.isoformat() if usage.latest else None,
                "latest_day_usage": usage.latest.usage if usage.latest else None,
                "current_month_total": usage.current_month_total,
                "latest_bill_month": (
                    usage.latest_bill.month.strftime("%Y-%m")
                    if usage.latest_bill
                    else None
                ),
                "latest_bill_usage": usage.latest_bill.usage
                if usage.latest_bill
                else None,
                "latest_bill_charge": (
                    usage.latest_bill.charge if usage.latest_bill else None
                ),
                "current_year_usage": usage.current_year_usage,
                "current_year_charge": usage.current_year_charge,
                "balance_available": usage.billing_account is not None,
                "meter_reading_available": usage.latest_month_meter is not None,
            }
        )

    last_exception = runtime.coordinator.last_exception
    return {
        "entry_title": entry.title,
        "options": dict(entry.options),
        "version": entry.version,
        "login": {
            "logged_in": session is not None,
            "user_id_suffix": _masked(session.user_id) if session else None,
            "session_expires_in_hours": (
                round((session.expires_at - time.time()) / 3600, 1) if session else None
            ),
            "account_count": len(api.accounts),
        },
        "device": {
            "model": api.profile.device_model,
            "android_release": api.profile.android_release,
            "datacenter": api.profile.datacenter,
            "token_cache_age_minutes": None,  # 由 token_time 推算过于精细，略
        },
        "accounts": accounts,
        "auth_error": entry.data.get(CONF_AUTH_ERROR),
        "last_update_exception": str(last_exception) if last_exception else None,
    }
