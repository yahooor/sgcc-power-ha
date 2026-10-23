"""诊断输出必须脱敏：不含手机号/密码/令牌/完整户号。"""

import asyncio
from datetime import date
from types import SimpleNamespace

from custom_components.sgcc_power.const import DOMAIN
from custom_components.sgcc_power.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.sgcc_power.models import (
    AccountBalance,
    AccountUsage,
    DailyReading,
    MonthlyBill,
    PowerAccount,
)


def _runtime() -> SimpleNamespace:
    usage = AccountUsage(
        account=PowerAccount.from_api(
            {"powerUserNo": "04302332076092", "powerUserNo_dst": "4302332076092"}
        ),
        readings=(DailyReading.from_api({"day": "20261023", "dayElePq": "13.82"}),),
        current_month_total=13.82,
        as_of=date(2026, 10, 24),
        monthly_bills=(
            MonthlyBill.from_api(
                {"ym": "202609", "monthPq": "432", "monthAmt": "286.12"}
            ),
        ),
        billing_account=AccountBalance.from_api(
            {"consType": "1", "sumMoney": "106.88"}
        ),
    )
    api = SimpleNamespace(
        login_session=SimpleNamespace(user_id="123456789", expires_at=2_000_000_000),
        accounts=[usage.account],
        profile=SimpleNamespace(
            device_model="sdk_gphone64_arm64",
            android_release="13",
            datacenter="99",
        ),
    )
    coordinator = SimpleNamespace(data={"4302332076092": usage}, last_exception=None)
    return SimpleNamespace(api=api, coordinator=coordinator)


def _entry() -> SimpleNamespace:
    return SimpleNamespace(
        entry_id="eid",
        title="国家电网电力 · 2432",
        options={"update_interval_hours": 12},
        version=1,
        data={"auth_error": None},
    )


def test_diagnostics_redacts_sensitive_values() -> None:
    hass = SimpleNamespace(data={DOMAIN: {"eid": _runtime()}})
    result = asyncio.run(async_get_config_entry_diagnostics(hass, _entry()))
    blob = str(result)
    # 完整户号 / userId 不能出现
    assert "4302332076092" not in blob
    assert "123456789" not in blob
    assert result["login"]["logged_in"] is True
    assert result["login"]["account_count"] == 1
    assert result["accounts"][0]["reading_days"] == 1
    assert result["accounts"][0]["latest_bill_month"] == "2026-09"
    assert result["accounts"][0]["id_suffix"].endswith("6092")


def test_diagnostics_handles_unloaded_entry() -> None:
    hass = SimpleNamespace(data={})
    result = asyncio.run(async_get_config_entry_diagnostics(hass, _entry()))
    assert result == {"error": "integration not loaded"}
