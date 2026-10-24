"""统计回填纯函数：清洗/增量/基线/滑动窗口。"""

from datetime import date

from custom_components.sgcc_power.models import DailyReading, PowerAccount
from custom_components.sgcc_power.statistics import (
    baseline_outdated,
    build_daily_stats,
    clean_series,
    cost_statistic_id,
    energy_statistic_id,
)


def _r(day: str, usage, charge=None):
    return DailyReading.from_api({"day": day, "dayElePq": usage, "thisAmt": charge})


def _account() -> PowerAccount:
    return PowerAccount.from_api(
        {"powerUserNo": "04302332076092", "powerUserNo_dst": "4302332076092"}
    )


def test_statistic_ids_use_external_format() -> None:
    assert (
        energy_statistic_id("4302332076092") == "sgcc_power:total_energy_4302332076092"
    )
    assert cost_statistic_id("4302332076092") == "sgcc_power:total_cost_4302332076092"


def test_clean_series_skips_invalid_and_zero() -> None:
    readings = (
        _r("20261003", 3.0),
        _r("20261001", 0.0),  # 零值跳过
        _r("20261002", None),  # 缺值跳过
        _r("20261005", 1.5),
        _r("20261004", -1.0),  # 负值跳过
    )
    series = clean_series(readings, "usage")
    assert series == [("2026-10-03", 3.0), ("2026-10-05", 1.5)]


def test_build_daily_stats_cumulative_and_stability() -> None:
    series = [("2026-10-01", 2.0), ("2026-10-02", 3.0), ("2026-10-03", 4.0)]
    records, last_day, total = build_daily_stats(
        series, last_imported_day=None, running_total=0.0, today=date(2026, 10, 6)
    )
    # 距今 ≥2 天的全部导入，sum 为逐日累计
    assert [r["sum"] for r in records] == [2.0, 5.0, 9.0]
    assert last_day == "2026-10-03"
    assert total == 9.0

    # 续跑：只导入游标之后、且距今 ≥2 天的
    records2, last2, total2 = build_daily_stats(
        series + [("2026-10-04", 1.0), ("2026-10-05", 1.0)],
        last_imported_day=last_day,
        running_total=total,
        today=date(2026, 10, 6),
    )
    assert [r["sum"] for r in records2] == [10.0]
    assert last2 == "2026-10-04"
    assert total2 == 10.0


def test_baseline_outdated_detection() -> None:
    series = [("2026-10-01", 2.0), ("2026-10-02", 3.0)]
    # 游标在窗口内且累计一致
    assert (
        baseline_outdated(series, last_imported_day="2026-10-02", expected_total=5.0)
        is False
    )
    # 累计对不上 → 全量重导
    assert (
        baseline_outdated(series, last_imported_day="2026-10-02", expected_total=9.9)
        is True
    )
    # 游标日早于窗口起点（窗口滑动）→ 无法重算，信任游标
    assert (
        baseline_outdated(series, last_imported_day="2026-09-15", expected_total=99.0)
        is None
    )
    # 无游标
    assert (
        baseline_outdated(series, last_imported_day=None, expected_total=0.0) is False
    )


def test_cost_series_empty_when_charge_missing() -> None:
    assert clean_series((_r("20261001", 5.0), _r("20261002", 6.0)), "charge") == []
    assert clean_series(
        (_r("20261001", 5.0, 2.5), _r("20261002", 6.0, 3.1)), "charge"
    ) == [("2026-10-01", 2.5), ("2026-10-02", 3.1)]
    assert _account().account_id
