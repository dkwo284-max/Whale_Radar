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


def _div_series(freq_days, amt, n_days=400, end=date(2026, 11, 5), price=50.0):
    ds, cs = _series(n_days=n_days, end=end, start_price=price, step=0.0)
    divs, d = [], date.fromisoformat(ds[-1]) - timedelta(days=3)
    while d.isoformat() >= ds[0]:
        divs.append((d.isoformat(), amt))
        d -= timedelta(days=freq_days)
    return ds, cs, divs


@pytest.mark.parametrize("gap,fq", [(7, "w"), (30, "m"), (91, "q"), (182, "h"), (365, "a")])
def test_dividend_frequency(gap, fq):
    from etf import dividend_stats
    ds, cs, divs = _div_series(gap, 0.1, n_days=800)
    assert dividend_stats(ds, cs, divs)["fq"] == fq


def test_dividend_yield_monthly():
    from etf import dividend_stats
    ds, cs, divs = _div_series(30, 0.25)  # 월 0.25 × 12~13회 / 50
    st = dividend_stats(ds, cs, divs)
    assert st["fq"] == "m" and 12 <= st["dn"] <= 13 and st["dy"] == pytest.approx(st["dn"] * 0.25 / 50 * 100)
    assert st["ld"] == 0.25 and st["lx"] == divs[0][0] and not st["ya"]


def test_dividend_young_fund_annualized():
    from etf import dividend_stats
    ds, cs, divs = _div_series(7, 0.1, n_days=120)
    st = dividend_stats(ds, cs, divs)
    assert st["ya"] and st["fq"] == "w" and 9 < st["dy"] < 12  # 주 0.1 × 52 ÷ 50 ≈ 10.4%
    assert st["dy"] == pytest.approx(0.1 * len(divs) * 365 / 120 / 50 * 100, rel=0.1)


def test_dividend_none():
    from etf import dividend_stats
    ds, cs = _series()
    assert dividend_stats(ds, cs, [])["dy"] is None


def test_make_row_price_vs_total():
    ds, adj = _series(step=0.001)
    raw = [c / (1 + 0.0002 * (len(adj) - i)) for i, c in enumerate(adj)]
    row, ser = make_row({"t": "D", "n": "Demo Dividend ETF", "c": "us", "l": "us"}, ds, adj, [100] * len(ds), raw, [(ds[-30], 0.5)])
    assert row["pr"][1] is not None and row["r"][7] is not None and row["dv"]["fq"] == "a" and ser["dv"] == [[ds[-30], 0.5]]


def test_div_union_and_filter():
    from etf import div_union, is_div_etf
    base = {"c": "us", "l": "us", "r": [1.0] * 8, "pr": [0, 0]}
    rows = [{**base, "y": f"D{i}", "th": "배당·인컴", "dv": {"dy": float(i)}} for i in range(40)]
    rows.append({**base, "y": "BOND", "th": "채권·현금", "dv": {"dy": 9.0}})
    assert not is_div_etf(rows[1]) and is_div_etf(rows[2]) and not is_div_etf(rows[-1])
    u = div_union(rows, n=5)
    assert "D39" in u and "BOND" not in u


def test_home_subset_keeps_top_and_bottom_per_country():
    import build_etf
    rows = [{"t": f"T{i}", "y": f"T{i}", "n": "x", "c": "us", "l": "us", "th": "a", "r": [float(i), 1.0]} for i in range(10)]
    rows.append({"t": "N", "y": "N", "n": "x", "c": "us", "l": "us", "th": "a", "r": [None, 1.0]})
    h = build_etf.home_subset({"generated_at": "g", "rows": rows})
    assert h["home"] and [r["t"] for r in h["rows"]] == ["T9", "T8", "T7", "T0", "T1", "T2"] and h["rows"][0]["r"] == [9.0]
