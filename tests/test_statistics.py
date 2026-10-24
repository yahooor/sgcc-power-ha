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


def test_cost_series_prefers_upstream_and_falls_back_to_price() -> None:
    from custom_components.sgcc_power.statistics import cost_series

    readings = (
        _r("20261001", 5.0, 2.9),  # 有上游日电费 → 直接用
        _r("20261002", 6.0, None),  # 无日电费 → 6.0 × 0.588 = 3.528
        _r("20261003", 0.0, None),  # 零用量跳过
        _r("20261004", None, None),  # 缺值跳过
    )
    assert cost_series(readings, 0.588) == [
        ("2026-10-01", 2.9),
        ("2026-10-02", 3.528),
    ]
    # price=0 关闭成本统计
    assert cost_series(readings, 0.0) == []


def test_price_change_detection() -> None:
    from custom_components.sgcc_power.statistics import price_changed

    assert price_changed(None, 0.588) is False  # 首次记录不算变更
    assert price_changed(0.588, 0.588) is False  # 未变
    assert price_changed(0.588, 0.638) is True  # 变了
    assert price_changed(0.588, 0.0) is True  # 关闭成本也是变更


def test_reset_cost_cursors_only_touches_cost() -> None:
    from types import SimpleNamespace

    from custom_components.sgcc_power.statistics import StatisticsImporter

    imp = StatisticsImporter(SimpleNamespace(), price=0.638)
    imp._cursors = {
        "_price": 0.588,
        "acct1": {
            "total_energy": {
                "last_imported_day": "2026-10-01",
                "last_imported_total": 100.0,
            },
            "total_cost": {
                "last_imported_day": "2026-10-01",
                "last_imported_total": 58.8,
            },
        },
        "acct2": {
            "total_cost": {
                "last_imported_day": "2026-09-01",
                "last_imported_total": 30.0,
            }
        },
    }
    imp._reset_cost_cursors()
    assert (
        imp._cursors["acct1"]["total_energy"]["last_imported_total"] == 100.0
    )  # 电量游标保留
    assert "total_cost" not in imp._cursors["acct1"]
    assert "total_cost" not in imp._cursors["acct2"]
    assert imp._cursors["_price"] == 0.588  # 顶层价格键不受影响


def _r_oct(day, usage):
    return _r(f"202610{day:02d}", usage)


def test_tiered_series_matches_official_esti_amt() -> None:
    """校准：2026-10（春秋月）官方 estiAmt=191.61，用量 315.71 kWh。

    阶梯口径 200×0.588 + 115.71×0.638 = 191.42，与官方差 <0.2%（约一天边界取整）。
    """
    from custom_components.sgcc_power.statistics import tiered_cost_series

    readings = [_r_oct(d, 315.71 / 22) for d in range(1, 23)]
    series = tiered_cost_series(readings, 0.588)
    total = round(sum(v for _, v in series), 2)
    assert total == 191.42
    assert abs(total - 191.61) / 191.61 < 0.005  # 与官方值偏差 <0.5%


def test_tiered_boundaries_and_month_reset() -> None:
    from custom_components.sgcc_power.statistics import tiered_cost_series

    readings = (
        # 10 月（春秋：二档 200/三档 350）：第一天 250 度 → 200×0.588 + 50×0.638
        _r_oct(1, 250.0),
        # 第二天 150 度：此前已 250 → 100×0.638 + 50×0.888
        _r_oct(2, 150.0),
        # 11 月也是春秋月，累计清零：100 度全一档
        _r("20261101", 100.0),
        # 7 月（冬夏：三档 450）：第一天 460 度 → 200×0.588 + 250×0.638 + 10×0.888
        _r("20260701", 460.0),
    )
    series = dict(tiered_cost_series(readings, 0.588))
    assert series["2026-10-01"] == round(200 * 0.588 + 50 * 0.638, 4)
    assert series["2026-10-02"] == round(100 * 0.638 + 50 * 0.888, 4)
    assert series["2026-11-01"] == round(100 * 0.588, 4)
    assert series["2026-07-01"] == round(200 * 0.588 + 250 * 0.638 + 10 * 0.888, 4)


def test_tiered_prefers_real_charge() -> None:
    from custom_components.sgcc_power.statistics import tiered_cost_series

    readings = (_r("20261001", 100.0, 55.5),)  # 上游日电费 55.5 优先
    assert tiered_cost_series(readings, 0.588) == [("2026-10-01", 55.5)]


def test_cost_series_tiered_flag_routed() -> None:
    from custom_components.sgcc_power.statistics import cost_series

    readings = (_r("20261001", 100.0),)
    flat = cost_series(readings, 0.588)
    tiered = cost_series(readings, 0.588, tiered=True)
    assert flat == [("2026-10-01", 58.8)]
    assert tiered == flat  # 100 度未过二档，两者一致
    assert cost_series(readings, 0.588, tiered=True) == [("2026-10-01", 58.8)]


def test_estimated_month_cost_current_month_only() -> None:
    from datetime import date

    from custom_components.sgcc_power.statistics import estimated_month_cost

    readings = (
        _r("20260930", 10.0),  # 上月不计
        _r("20261001", 250.0),  # 一天跨到二档：200×0.588 + 50×0.638
        _r("20261020", 14.0),  # 此前已 250 → 全二档 0.638
        _r("20261101", 5.0),  # 未来月不计
    )
    # 固定单价：264 × 0.588 = 155.232
    assert estimated_month_cost(readings, 0.588, today=date(2026, 10, 25)) == 155.23
    # 阶梯：(117.6 + 31.9) + 14×0.638 = 158.432
    assert (
        estimated_month_cost(readings, 0.588, tiered=True, today=date(2026, 10, 25))
        == 158.43
    )
    # 关闭估算
    assert estimated_month_cost(readings, 0.0) is None
    # 无当月数据
    assert (
        estimated_month_cost((_r("20260930", 10.0),), 0.588, today=date(2026, 10, 25))
        is None
    )
