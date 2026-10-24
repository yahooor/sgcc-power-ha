"""Data model parsing from upstream-shaped fixtures."""

from datetime import date

from custom_components.sgcc_power.models import (
    AccountBalance,
    AccountUsage,
    DailyReading,
    MonthlyBill,
    PowerAccount,
    YearlyBilling,
)


def _reading(day: str, usage: float, **tou: float) -> DailyReading:
    return DailyReading.from_api(
        {
            "day": day,
            "dayElePq": usage,
            "thisTPq": tou.get("tip", 0),
            "thisPPq": tou.get("peak", 0),
            "thisNPq": tou.get("flat", 0),
            "thisVPq": tou.get("valley", 0),
        }
    )


def test_daily_reading_tou_fields() -> None:
    reading = _reading("20261020", 13.82, peak=5.42, valley=4.75, flat=3.65)
    assert reading.day == date(2026, 10, 20)
    assert reading.usage == 13.82
    assert reading.peak == 5.42
    assert reading.valley == 4.75
    assert reading.flat == 3.65
    assert reading.tip == 0


def test_monthly_bill_prefers_top_level_and_falls_back_to_elelist() -> None:
    bill = MonthlyBill.from_api(
        {"ym": "202609", "monthPq": "432.0", "monthAmt": "286.12"}
    )
    assert bill.month == date(2026, 9, 1)
    assert bill.usage == 432.0
    assert bill.charge == 286.12

    merged = MonthlyBill.from_api(
        {
            "ym": "202609",
            "eleList": [{"pq": "100", "amt": "60"}, {"pq": "32", "amt": "20"}],
        }
    )
    assert merged.usage == 132
    assert merged.charge == 80


def test_yearly_billing_deduplicates_months() -> None:
    billing = YearlyBilling.from_api(
        {
            "yearPq": "2191.64",
            "yearAmt": "1316.46",
            "list": [
                {"ym": "202609", "monthPq": "432"},
                {"ym": "202609", "monthPq": "440"},
                {"ym": "202608", "monthPq": "400"},
            ],
        },
        2026,
    )
    assert [bill.month.month for bill in billing.bills] == [8, 9]
    assert billing.usage == 2191.64
    assert billing.bills[-1].usage == 440


def test_account_balance_semantics() -> None:
    prepaid = AccountBalance.from_api(
        {"consType": "1", "sumMoney": "106.88", "prepayBal": "106.88"}
    )
    assert prepaid.balance == 106.88
    assert prepaid.amount_due is None

    postpaid = AccountBalance.from_api(
        {"consType": "2", "sumMoney": "86.4", "accountBalance": "0"}
    )
    assert postpaid.balance is None
    assert postpaid.amount_due == 86.4


def test_month_sum_over_tou_fields() -> None:
    usage = AccountUsage(
        account=PowerAccount.from_api(
            {"powerUserNo": "04302332076092", "powerUserNo_dst": "4302332076092"}
        ),
        readings=(
            _reading("20260930", 9),  # 上个月不计入月汇总
            _reading("20261001", 10, peak=4, valley=3, flat=3),
            _reading("20261020", 13.82, peak=5.42, valley=4.75, flat=3.65),
        ),
        current_month_total=None,
        as_of=date(2026, 10, 24),
    )
    assert usage.month_sum("usage") == 23.82
    assert usage.month_sum("peak") == 9.42
    assert usage.month_sum("valley") == 7.75
    assert usage.latest.day == date(2026, 10, 20)


def test_account_balance_parses_esti_amt() -> None:
    """本月已产生电费（estiAmt）——tiejiang 实测恒等式 sumMoney = prepayBal - estiAmt。"""
    balance = AccountBalance.from_api(
        {
            "consType": "1",
            "sumMoney": "111.45",
            "prepayBal": "297.05",
            "estiAmt": "185.60",
            "historyOwe": "0",
        }
    )
    assert balance.esti_amt == 185.60
    assert balance.history_owe == 0.0
    # 上游恒等式校验（容差浮点）
    assert (
        abs(
            (balance.prepay_balance or 0)
            - (balance.sum_money or 0)
            - (balance.esti_amt or 0)
        )
        < 0.01
    )
    # 不返回 estiAmt 的地区
    missing = AccountBalance.from_api({"consType": "1", "sumMoney": "10"})
    assert missing.esti_amt is None
