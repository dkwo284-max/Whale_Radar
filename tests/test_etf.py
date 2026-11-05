from datetime import date, timedelta

import pytest

from etf import PERIODS, drop_stale, exclusion_reason, make_row, period_returns, rank_union, spark, theme_of, us_listed_country, weekly


@pytest.mark.parametrize("name,why", [
    ("Direxion Daily Semiconductor Bull 3X Shares", "leveraged"),
    ("ProShares UltraPro QQQ", "leveraged"),
    ("ProShares Ultra S&P500", "leveraged"),
    ("ProShares UltraShort 20+ Year Treasury", "leveraged"),
    ("GraniteShares 2x Long NVDA Daily ETF", "leveraged"),
    ("Defiance Daily Target 2X Long MSTR ETF", "leveraged"),
    ("T-Rex 2X Inverse Tesla Daily Target ETF", "leveraged"),
    ("Direxion Daily FTSE China Bear 3X Shares", "leveraged"),
    ("ProShares Short S&P500", "inverse"),
    ("AdvisorShares Ranger Equity Bear ETF", "inverse"),
    ("Tuttle Capital Short Innovation ETF", "inverse"),
    ("Simplify US Equity PLUS Downside Convexity ETF", None),
    ("iShares VIX Short-Term Futures ETN", "vix"),
    ("ProShares VIX Mid-Term Futures ETF", "vix"),
    ("Simplify Volatility Premium ETF", "vix"),
    ("iShares MSCI USA Min Vol Factor ETF", None),
    ("Invesco S&P 500 Low Volatility ETF", None),
    ("VictoryShares US 500 Volatility Wtd ETF", None),
    ("iShares 1-3 Year Treasury Bond ETF", None),
    ("Vanguard Short-Term Bond ETF", None),
    ("SPDR Bloomberg 1-3 Month T-Bill ETF", None),
    ("JPMorgan Ultra-Short Income ETF", None),
    ("PIMCO Enhanced Short Maturity Active ETF", None),
    ("iShares Short Treasury Bond ETF", None),
    ("AGFiQ US Market Neutral Anti-Beta Long/Short", None),
    ("SPDR S&P 500 ETF Trust", None),
    ("VanEck Semiconductor ETF", None),
    ("KraneShares CSI China Internet ETF", None),
])
def test_exclusion(name, why):
    assert exclusion_reason(name) == why


@pytest.mark.parametrize("name,c", [
    ("KraneShares CSI China Internet ETF", "cn"),
    ("iShares China Large-Cap ETF", "cn"),
    ("Global X MSCI China Consumer Discretionary ETF", "cn"),
    ("Franklin FTSE Hong Kong ETF", "cn"),
    ("iShares MSCI Emerging Markets ex China ETF", "other"),
    ("iShares MSCI India ETF", "in"),
    ("Columbia India Consumer ETF", "in"),
    ("Global X Uranium ETF", "us"),
    ("Global X Copper Miners ETF", "us"),
    ("iShares MSCI Japan ETF", "other"),
    ("Vanguard FTSE Developed Markets ETF", "other"),
    ("SPDR S&P 500 ETF Trust", "us"),
    ("iShares Semiconductor ETF", "us"),
    ("SPDR Gold Shares", "us"),
])
def test_country(name, c):
    assert us_listed_country(name) == c


def test_theme():
    assert theme_of("VanEck Semiconductor ETF") == "반도체"
    assert theme_of("iShares Bitcoin Trust ETF") == "크립토"
    assert theme_of("SPDR S&P Regional Banking ETF") == "금융"
    assert theme_of("Global X Uranium ETF") == "원자재"
    assert theme_of("iShares 20+ Year Treasury Bond ETF") == "채권·현금"
    assert theme_of("SPDR S&P 500 ETF Trust") == "시장 대표지수"
    assert theme_of("Something Unusual Trust") == "기타"


def _series(n_days=400, end=date(2026, 11, 5), start_price=100.0, step=0.001):
    ds, cs = [], []
    d = end - timedelta(days=n_days)
    p = start_price
    while d <= end:
        if d.weekday() < 5:
            ds.append(d.isoformat())
            cs.append(p)
            p *= 1 + step
        d += timedelta(days=1)
    return ds, cs


def test_period_returns_basic():
    ds = ["2025-12-31", "2026-01-02", "2026-01-05", "2026-01-06"]
    cs = [100.0, 110.0, 99.0, 121.0]
    r = period_returns(ds, cs)
    assert r["1d"] == pytest.approx((121 / 99 - 1) * 100, abs=0.01)
    assert r["ytd"] == pytest.approx(21.0, abs=0.01)
    assert r["7d"] is None  # 기준일(12-30) 이전 데이터가 없으면 이력 부족
    assert r["30d"] is None


def test_period_returns_short_history_none():
    ds, cs = _series(n_days=40)
    r = period_returns(ds, cs)
    assert r["30d"] is not None and r["90d"] is None and r["1y"] is None


def test_period_returns_prev_day():
    ds, cs = _series()
    now = period_returns(ds, cs)
    prev = period_returns(ds, cs, len(ds) - 2)
    assert now["1d"] == pytest.approx(0.1, abs=0.01) and prev["1d"] == pytest.approx(0.1, abs=0.01)
    assert now["1y"] is not None and now["1y"] > now["180d"] > now["30d"] > 0


def test_make_row_and_spark_weekly():
    ds, cs = _series()
    row, wk = make_row({"t": "AAA", "n": "Demo Semiconductor ETF", "c": "us", "l": "us"}, ds, cs, [1000] * len(ds))
    assert row["th"] == "반도체" and len(row["r"]) == len(PERIODS) == len(row["rp"])
    assert row["sp"][0] == 1000 and len(row["sp"]) == 30
    assert wk["v"][0] == 1000 and 50 <= len(wk["v"]) <= 53 and wk["end"] == ds[-1]
    assert make_row({"t": "B", "n": "x", "c": "us", "l": "us"}, ds[:10], cs[:10], [1] * 10) is None


def test_spark_short():
    assert spark([1.0]) == []


def test_drop_stale():
    a = {"c": "us", "l": "us", "d": "2026-11-05"}
    b = {"c": "us", "l": "us", "d": "2026-10-20"}
    c = {"c": "cn", "l": "local", "d": "2026-10-20"}
    keep, n = drop_stale([a, b, c])
    assert keep == [a, c] and n == 1


def test_rank_union_covers_top_and_bottom():
    rows = [{"y": f"T{i}", "c": "us", "l": "us", "r": [float(i)] * len(PERIODS)} for i in range(100)]
    u = rank_union(rows, n=5)
    assert {"T0", "T4", "T95", "T99"} <= u and "T50" not in u


def test_weekly_empty():
    assert weekly([], []) == {"end": None, "v": []}


def test_fix_splits_unadjusted_split():
    from etf import fix_splits
    cs = [300.0, 303.0, 306.0, 102.5, 103.0]  # 3:1 분할이 반영 안 된 시계열
    out, vs = fix_splits(cs, [10.0] * 5)
    assert out[2] == pytest.approx(102.0) and out[3] == 102.5 and vs[0] == pytest.approx(30.0)
    r = period_returns(["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"], out)
    assert abs(r["1d"]) < 1


def test_fix_splits_keeps_normal_moves():
    from etf import fix_splits
    cs = [100.0, 80.0, 70.0, 95.0]
    assert fix_splits(cs, [1.0] * 4)[0] == cs


def test_glitch_row_dropped():
    ds, cs = _series()
    cs = cs[:-1] + [cs[-1] * 0.23]  # 정수배가 아닌 급락 = 시세 오류
    assert make_row({"t": "X", "n": "x", "c": "us", "l": "us"}, ds, cs, [1] * len(ds)) == "glitch"
