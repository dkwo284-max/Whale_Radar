from datetime import date, timedelta

import pytest

from macro import (I1Y, I90, MP, changes, flow_ratio, hegemony_regions, quadrant, rank_candidates, rel, rel_at, rets, rs_weekly,
                   score_candidate, score_history, smart_money, to_nzd, vol_dd)


def _series(n_days=420, end=date(2026, 11, 5), start=100.0, step=0.001):
    ds, cs, d, p = [], [], end - timedelta(days=n_days), start
    while d <= end:
        if d.weekday() < 5:
            ds.append(d.isoformat())
            cs.append(p)
            p *= 1 + step
        d += timedelta(days=1)
    return ds, cs


def test_rets_and_rel():
    ds, cs = _series()
    r = rets(ds, cs)
    assert len(r) == len(MP) and all(x is not None for x in r) and r[I1Y] > r[I90] > 0
    b = rets(*_series(step=0.0005))
    d = rel(r, b)
    assert d[I90] == pytest.approx(r[I90] - b[I90], abs=0.01)
    assert rel([None, 1.0], [1.0, 2.0]) == [None, -1.0]


def test_to_nzd_converts_with_fx_and_ffill():
    ds = ["2026-11-02", "2026-11-03", "2026-11-04"]
    cs = [100.0, 100.0, 100.0]
    # NZDUSD: 0.5 → 0.6 (뉴질랜드달러 강세) 이면 달러 자산의 NZD 가치는 내려간다. 마지막 환율 날짜가 하루 빠져도 직전 값을 쓴다.
    nd, nc = to_nzd(ds, cs, ["2026-11-02", "2026-11-03"], [0.5, 0.6])
    assert nd == ds and nc == pytest.approx([200.0, 100 / 0.6, 100 / 0.6])
    nd, nc = to_nzd(ds, cs, ["2026-11-03"], [0.6])
    assert nd == ds[1:]  # 환율이 시작하기 전 날짜는 뺀다


def test_nzd_return_against_known_case():
    ds, cs = _series(step=0.0)
    fx = [0.60 if d < "2026-09-01" else 0.66 for d in ds]  # 9월에 뉴질랜드달러 10% 강세
    nd, nc = to_nzd(ds, cs, ds, fx)
    expect = (0.60 / 0.66 - 1) * 100  # 달러 자산은 그대로여도 뉴질랜드달러로는 약 −9.1%
    assert rets(nd, nc)[I90] == pytest.approx(expect, abs=0.01) and rets(nd, nc)[I1Y] == pytest.approx(expect, abs=0.01)


def test_rs_weekly_and_rel_at():
    ds, cs = _series(step=0.002)
    bds, bcs = _series(step=0.001)
    rs = rs_weekly(ds, cs, bds, bcs)
    assert rs[0] == 100.0 and 50 <= len(rs) <= 53 and rs[-1] > rs[0]
    assert rel_at(ds, cs, bds, bcs, 28) > 0
    assert rel_at(ds[:10], cs[:10], bds, bcs, 28) is None
    assert rs_weekly([], [], bds, bcs) == []


def test_changes_for_yields():
    ds, vs = _series(step=0.0)
    vs = [4.0 if d < "2026-09-01" else 4.5 for d in ds]
    c = changes(ds, vs)
    assert c[I90] == pytest.approx(0.5, abs=0.01) and c[I1Y] == pytest.approx(0.5, abs=0.01)


def test_vol_dd_and_flow_ratio():
    ds, cs = _series(step=0.0)
    cs = cs[:-10] + [c * 0.8 for c in cs[-10:]]
    vol, dd = vol_dd(cs)
    assert dd == pytest.approx(-20.0, abs=0.1) and vol > 0
    assert vol_dd([1.0] * 5) == (None, None)
    vs = [100.0] * (len(cs) - 20) + [200.0] * 20
    flat = [100.0] * len(cs)
    assert flow_ratio(flat, vs) > 1.5 and flow_ratio(flat, [100.0] * len(cs)) == 1.0
    assert flow_ratio([1.0] * 10, [1.0] * 10) is None


HEG = {"regions": {
    "eu": {"n": "유럽", "cells": [{"cat": "성장", "signal": "◆"}, {"cat": "증시", "signal": "미확인"}, {"cat": "통화·금융", "signal": "▲"},
                                  {"cat": "안보·군사", "signal": "▲"}, {"cat": "기술·무역", "signal": "미확인"}]},
    "us": {"n": "미국", "cells": [{"cat": "성장", "signal": "◆", "value": ""}, {"cat": "증시", "signal": "▲", "value": ""},
                                  {"cat": "통화·금융", "signal": "▼", "value": "DXY 101.8"}, {"cat": "안보·군사", "signal": "▼"},
                                  {"cat": "기술·무역", "signal": "◆"}]},
    "xx": {"n": "미평가", "cells": [{"cat": c, "signal": "미확인"} for c in ["성장", "증시", "통화·금융", "안보·군사", "기술·무역"]]},
}, "log": [{"date": "2026-11-05", "region": "eu", "score": 1}, {"date": "2026-10-29", "region": "eu", "score": 0}]}


def test_hegemony_score_from_cells_and_history():
    r = hegemony_regions(HEG)
    assert r["eu"]["score"] == 2 and r["eu"]["ups"] == ["통화·금융", "안보·군사"] and r["eu"]["unknown"] == ["증시", "기술·무역"]
    assert r["us"]["score"] == -1
    h = score_history(HEG)
    assert [x["date"] for x in h["eu"]] == ["2026-10-29", "2026-11-05"]


@pytest.mark.parametrize("s,r,q", [(2, 5.0, "trend"), (2, -3.0, "early"), (-1, 4.0, "heat"), (-1, -2.0, "avoid"), (0, 1.0, "heat"), (None, 3.0, None), (1, None, None)])
def test_quadrant(s, r, q):
    assert quadrant(s, r) == q


DASH = {"investors": [
    {"id": "a", "positions": [{"ticker": "BABA", "signal": "BUY"}, {"ticker": "TSM", "signal": "SELL"}, {"ticker": "AAPL", "signal": "BUY"}]},
    {"id": "b", "positions": [{"ticker": "BABA", "signal": "SELL"}, {"ticker": "", "issuer": "Asml Hldg Nv", "signal": "BUY"}]},
    {"id": "c", "positions": [{"ticker": "EWZ", "signal": "BUY"}, {"ticker": "EWZ", "signal": "BUY"}]},
]}
CFG = {"adr": {"cn": ["BABA"], "ot": ["TSM"], "la": ["EWZ"], "eu": []}, "adr_issuer": {"eu": ["asml"]},
       "thresholds": {"min_turnover_usd": 5_000_000, "flow_ratio": 1.15}, "risk": {"eu": "유럽 리스크", "gl": "금 리스크"},
       "sig_override": {"GLD": {"rg": "us", "cat": "통화·금융", "want": "▼"}}}


def test_smart_money_regions_and_issuer_fallback():
    sm = smart_money(DASH, CFG)
    assert sm["cn"]["buy"] == 1 and sm["cn"]["sell"] == 1 and sm["ot"]["sell"] == 1
    assert sm["eu"]["buy"] == 1 and sm["eu"]["buys"] == ["ASML"]
    assert sm["la"]["buy"] == 1  # 같은 투자자가 두 줄 가져도 한 명
    assert "AAPL" not in sm["_t"] and sm["_t"]["BABA"] == {"buy": 1, "sell": 1}


def _item(**kw):
    base = {"y": "EWG", "n": "독일", "k": "country", "rg": "eu", "cur": "EUR", "rl": [0, 0, 5.0, 0, 0, 0], "r": [0] * 6, "rn": [0] * 6,
            "fx1y": 6.1, "to": 9_000_000, "fr": 1.3, "vol": 20.0, "dd": -4.0}
    base["rl"] = [0.0, -1.0, 5.0, 0.0, 0.0, 0.0]
    base.update(kw)
    return base


def test_candidate_all_pass_is_cand():
    regs = hegemony_regions(HEG)
    c = score_candidate(_item(), regs, smart_money(DASH, CFG), CFG)
    assert c["status"] == "cand" and c["pass"] == 4 and len(c["pros"]) == 3 and c["risk"][0].startswith("환율")
    assert "거래대금" in c["detail"]["flow"]


def test_country_does_not_borrow_region_wide_13f():
    regs = hegemony_regions(HEG)
    sm = smart_money({"investors": [{"id": "a", "positions": [{"ticker": "", "issuer": "Asml Hldg Nv", "signal": "BUY"}]}]}, CFG)
    c = score_candidate(_item(fr=1.0), regs, sm, CFG)  # 거래대금도 늘지 않았다 → 독일 ETF 는 큰손 근거 없음
    assert c["checks"]["flow"] is False and c["status"] == "watch" and "큰손·거래대금" in c["con"]
    c2 = score_candidate(_item(fr=1.0, k="region", y="VGK"), regs, sm, CFG)  # 지역 대표 ETF 는 지역 집계를 쓴다
    assert c2["checks"]["flow"] is True


def test_candidate_two_misses_is_no_and_no_signal_caps_at_watch():
    regs = hegemony_regions(HEG)
    c = score_candidate(_item(rl=[0, 0, -3.0, 0, 0, 0], to=1_000_000), regs, {}, CFG)
    assert c["status"] == "no" and c["checks"]["rel"] is False and c["checks"]["liq"] is False
    jp = score_candidate(_item(y="EWJ", rg="ot", fr=1.5), regs, {}, CFG)
    assert jp["checks"]["sig"] is None and jp["status"] == "watch" and "신호" in jp["con"]
    xx = score_candidate(_item(y="ZZZ", rg="xx"), regs, {}, CFG)
    assert xx["checks"]["sig"] is None


def test_gold_signal_follows_us_monetary_cell():
    regs = hegemony_regions(HEG)
    g = score_candidate(_item(y="GLD", rg="gl", k="global", cur=None), regs, {}, CFG)
    assert g["checks"]["sig"] is True and "통화·금융 ▼" in g["detail"]["sig"] and g["risk"][-1] == "정책: 금 리스크"


def test_rank_candidates_orders_by_status_then_relative_return():
    cs = [{"status": "watch", "r90": 9.0}, {"status": "cand", "r90": 1.0}, {"status": "cand", "r90": 4.0}, {"status": "no", "r90": 20.0}]
    assert [(c["status"], c["r90"]) for c in rank_candidates(cs)] == [("cand", 4.0), ("cand", 1.0), ("watch", 9.0), ("no", 20.0)]
