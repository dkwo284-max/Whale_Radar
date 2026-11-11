from datetime import date, timedelta

import newbuys as nb
import perf


def days(start: str, n: int) -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def q(period: str, h: dict[str, float]) -> dict:
    d = date.fromisoformat(period) + timedelta(days=40)
    return {"period": period, "filed": d.isoformat(), "total": sum(h.values()), "h": {c: {"v": v, "n": c + " INC"} for c, v in h.items()}}


P = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
TK = {c: c for c in "ABCDEFGX"}


def world():
    ds = days("2024-01-01", 800)
    grow = lambda r: perf.Series(ds, [100 * (1 + r) ** i for i in range(len(ds))])  # noqa: E731
    px = {"A": grow(0.0005), "B": grow(0.002), "C": grow(-0.001), "D": grow(0.002), "E": grow(0.0005), "F": grow(-0.001), "G": grow(0.002), "SPY": grow(0.0005)}
    good = [q(P[0], {"A": 100}), q(P[1], {"A": 80, "B": 20}), q(P[2], {"A": 80, "B": 20}), q(P[3], {"A": 70, "B": 15, "D": 15}),
            q(P[4], {"A": 70, "D": 30}), q(P[5], {"A": 70, "D": 30}), q(P[6], {"A": 70, "D": 30}), q(P[7], {"A": 60, "D": 20, "G": 20})]
    bad = [q(P[0], {"E": 100}), q(P[1], {"E": 80, "C": 20}), q(P[2], {"E": 100}), q(P[3], {"E": 80, "F": 20}),
           q(P[4], {"E": 99.5, "C": 0.5}), q(P[5], {"E": 90, "C": 10}), q(P[6], {"E": 100}), q(P[7], {"E": 80, "G": 12, "C": 8})]
    return {"good": good, "bad": bad}, px, ds[-1]


def test_events_classify_new_reentry_and_scaleup():
    per, _, _ = world()
    ev = {(e["period"], e["t"]): e["type"] for e in nb.events(per["bad"], TK)}
    assert ev[(P[1], "C")] == "new" and ev[(P[3], "F")] == "new"
    assert (P[4], "C") not in ev                       # 0.5% 는 최소 비중 미만
    assert ev[(P[5], "C")] == "scaleup"                 # 소량 보유 → 10%
    assert ev[(P[7], "C")] == "reentry" and ev[(P[7], "G")] == "new"
    assert all(e["period"] != P[0] for e in nb.events(per["good"], TK))   # 첫 분기는 비교 대상이 없다


def test_scorecard_ranks_by_forward_excess_after_filing():
    per, px, today = world()
    card, types, _ = nb.scorecard(per, TK, px, today, {"good": "잘 고르는 대가", "bad": "못 고르는 대가"})
    assert [r["id"] for r in card] == ["good", "bad"]
    g, b = card
    assert g["m6"]["mean"] > 0 > b["m6"]["mean"] and g["m6"]["win"] == 1.0 and b["m6"]["win"] == 0.0
    assert g["m6"]["n"] == 2 and abs(g["m6"]["shrunk"]) < abs(g["m6"]["mean"])   # 표본이 적으면 0 쪽으로 당긴다
    assert g["m12"]["n"] == 2 and g["best"][0]["t"] in ("B", "D") and types["new"]["n"] >= 3
    assert not g["top"]                                                          # 표본 5건 미만이면 '상위 대가'로 내세우지 않는다
    assert nb.stats([])["n"] == 0 and nb.stats([0.1])["ci"] is None


def test_current_picks_score_price_gap_and_live_combo(monkeypatch):
    per, px, today = world()
    monkeypatch.setattr(nb, "SHRINK", 0)
    out, ledger = nb.build(per, TK, px, "2026-02-20", {}, None)
    picks = {p["t"]: p for p in out["now"]["picks"]}
    assert out["now"]["period"] == P[7] and set(picks) == {"G", "C"}
    assert len(picks["G"]["buyers"]) == 2 and picks["G"]["score"] > picks["C"]["score"]   # 좋은 대가도 샀고, 두 명이 함께 샀다
    assert picks["G"]["vs_q"] > 0 and picks["G"]["vs_filed"] > 0                          # 분기 평균가·공시일보다 오른 상태
    assert picks["C"]["buyers"][0]["type"] == "reentry"
    assert out["now"]["combo"] == [] and "live" not in out and ledger == {"legs": []}     # 상위 대가 조건(표본 5건)을 못 채워 추적하지 않는다
    monkeypatch.setattr(nb, "TOP_GURUS", 5)
    card, _, ev = nb.scorecard(per, TK, px, "2026-02-20", {})
    card[0]["top"] = True
    now = nb.current_picks(per, TK, px, "2026-02-20", card, ev)
    assert [p["t"] for p in now["picks"] if p["by_top"]] == ["G"]
