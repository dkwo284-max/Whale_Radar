from datetime import date, timedelta

import perf
import strategy as st


def days(start: str, n: int) -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def q(period: str, h: dict[str, float]) -> dict:
    return {"period": period, "filed": st.plus_days(period, 40), "total": sum(h.values()), "h": {c: {"v": v, "n": c + " INC"} for c, v in h.items()}}


PERIODS = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
TK = {c: c for c in "ABCDEFG"}


def world():
    ds = days("2024-03-01", 800)
    grow = lambda r: perf.Series(ds, [100 * (1 + r) ** i for i in range(len(ds))])  # noqa: E731
    px = {"A": grow(0.002), "B": grow(0.001), "C": grow(0.0), "D": grow(-0.001), "E": grow(0.0015), "SPY": grow(0.0005)}
    per = {
        "win": [q(p, {"A": 70, "E": 30}) for p in PERIODS],
        "mid": [q(p, {"B": 60, "A": 30, "E": 10}) for p in PERIODS],
        "lose": [q(p, {"D": 80, "C": 20}) for p in PERIODS[:-1]] + [q(PERIODS[-1], {"D": 50, "C": 20, "E": 30})],
    }
    return per, px, ds[-1]


def test_schedule_and_snapshot_use_only_published_filings():
    per, px, today = world()
    s = st.schedule(per, today, "follow")
    assert s[0] == {"period": "2024-03-31", "date": "2024-05-17"}
    late = {"x": [q("2024-03-31", {"A": 1}), {**q("2024-06-30", {"B": 1}), "filed": "2024-09-30"}]}
    snap = st.snapshot(late, "2024-08-16", "follow", "2024-03-31")
    assert snap["x"][0]["period"] == "2024-03-31"          # 늦게 낸 공시는 아직 모른다
    assert st.snapshot(late, "2024-08-16", "qe", "2024-03-31")["x"][0]["period"] == "2024-06-30"
    assert st.snapshot(late, "2025-08-16", "follow", "2025-03-31") == {}  # 공시가 끊기면 빠진다


def test_pick_rules():
    ws = {"a": {"A": .6, "B": .4}, "b": {"A": .5, "C": .5}, "c": {"A": .2, "B": .3, "D": .5}}
    assert st.pick_consensus(ws, 2) == {"A": 0.6, "B": 0.4}
    assert st.pick_conviction(ws, 1) == {"A": 2 / 3, "D": 1 / 3}
    w, g = st.pick_momentum(ws, {"a": .1, "b": .3, "c": None}, 1, 10)
    assert g == ["b"] and w == {"A": .5, "C": .5}
    new = st.new_buys(q("2024-06-30", {"A": 50, "B": 49.5, "C": 0.5}), q("2024-03-31", {"A": 100}), TK, 0.01)
    assert set(new) == {"B"} and st.new_buys(q("2024-06-30", {"A": 1}), None, TK, 0.01) == {}


def test_build_end_to_end_momentum_follows_the_winner():
    per, px, today = world()
    out = st.build(per, TK, px, today, {"win": "승자", "mid": "중간", "lose": "패자"})
    f = out["bases"]["follow"]
    assert f["rebalances"] == len(PERIODS) - st.LOOKBACK and set(f["strategies"]) == set(st.ORDER)
    mom = f["strategies"]["momentum"]
    assert f["now"]["gurus"][0] == "win" and mom["metrics"]["cagr"] > mom["metrics"]["spy_cagr"]
    sched = st.schedule(per, today, "follow")
    top1 = st.portfolios(per, TK, px, sched, "follow", {"momentum": {"top": 1}})
    assert all(p["gurus"] == ["win"] for p in top1)
    assert st.run(top1, px, today, "momentum")["metrics"]["total"] > f["strategies"]["all"]["metrics"]["total"]
    assert abs(sum(x["w"] for x in f["now"]["portfolios"]["blend"]) - 1) < 0.01
    assert {x["t"] for x in f["now"]["portfolios"]["newbuy"]} == {"E"}   # 마지막 분기에 lose 가 새로 산 종목
    assert f["now"]["portfolios"]["consensus"] and "win" in f["now"]["portfolios"]["momentum"][0]["by"]
    assert f["now"]["next"] == "2026-05-17"
    assert out["recommend"]["pick"] in st.ORDER and out["gurus"][0]["id"] == "win"
    assert "qe" in out["bases"] and out["bases"]["qe"]["start"] == "2025-03-31"
    assert f["sensitivity"]["momentum"]["n"] >= 1


def test_empty_selection_holds_benchmark():
    per, px, today = world()
    sched = st.schedule(per, today, "follow")
    ports = st.portfolios(per, TK, px, sched, "follow", {"consensus": {"min_holders": 9}})
    r = st.run(ports, px, today, "consensus")
    assert all(abs(x["ret"] - x["spy"]) < 1e-9 for x in r["rows"])
