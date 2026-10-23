"""旧版 state_grid.config 兼容结构。"""

from datetime import date

from custom_components.sgcc_power.compat import build_legacy_power_user
from custom_components.sgcc_power.models import (
    AccountBalance,
    AccountUsage,
    DailyReading,
    MeterReading,
    MonthlyBill,
    PowerAccount,
)


def _usage() -> AccountUsage:
    account = PowerAccount.from_api(
        {
            "powerUserNo": "04302332076092",
            "powerUserNo_dst": "4302332076092",
            "consName": "*骞",
            "elecAddr": "湖南省张家界市某某小区",
        }
    )
    readings = tuple(
        DailyReading.from_api(item)
        for item in (
            {
                "day": "20260929",
                "dayElePq": "11.0",
                "thisPPq": "4",
                "thisVPq": "4",
                "thisNPq": "3",
            },
            {
                "day": "20261019",
                "dayElePq": "12.1",
                "thisPPq": "5",
                "thisVPq": "4",
                "thisNPq": "3.1",
            },
            {
                "day": "20261020",
                "dayElePq": "13.82",
                "thisPPq": "5.42",
                "thisVPq": "4.75",
                "thisNPq": "3.65",
            },
        )
    )
    bills = (
        MonthlyBill.from_api(
            {"ym": "202609", "monthPq": "432.0", "monthAmt": "286.12"}
        ),
    )
    return AccountUsage(
        account=account,
        readings=readings,
        current_month_total=25.92,
        as_of=date(2026, 10, 24),
        monthly_bills=bills,
        current_year_usage=2191.64,
        current_year_charge=1316.46,
        billing_account=AccountBalance.from_api(
            {"consType": "1", "sumMoney": "106.88", "date": "2026-10-24"}
        ),
        latest_month_meter=MeterReading(day=date(2026, 9, 30), reading=10585.64),
    )


def test_legacy_power_user_shape_matches_xiaoshi_reader() -> None:
    user = build_legacy_power_user(_usage())
    assert user["consNo_dst"] == "4302332076092"
    assert user["balance"] == 106.88
    assert user["consName_dst"] == "*骞"
    assert user["daily_lasted_date"] == "2026-10-20"

    daily = user["daily_bill_list"]
    assert daily[0]["day"] == "20260929"
    assert daily[2]["dayElePq"] == 13.82
    assert set(daily[2]) == {
        "day",
        "dayElePq",
        "thisTPq",
        "thisPPq",
        "thisNPq",
        "thisVPq",
    }

    month = user["month_bill_list"][0]
    assert month["month"] == "202609"
    assert month["monthEleNum"] == 432.0
    assert month["monthEleCost"] == 286.12
    for key in (
        "month_t_ele_num",
        "month_p_ele_num",
        "month_n_ele_num",
        "month_v_ele_num",
    ):
        assert key in month
    assert month["daily_ele"][0]["dayElePq"] == 11.0

    assert user["year_ele_num"] == 2191.64
    assert user["year_ele_cost"] == 1316.46
    assert user["last_month_ele_num"] == 432.0
    assert user["last_month_meter_num"] == 10585.64
