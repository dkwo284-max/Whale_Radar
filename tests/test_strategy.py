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


def test_schedule_skips_quarters_with_too_few_investors_and_merges_share_classes():
    per, px, today = world()
    per["win"] = [q("2023-12-31", {"A": 1})] + per["win"]      # 한 명만 있는 옛 분기
    assert st.schedule(per, today, "qe")[0]["period"] == "2024-03-31"
    assert st.weights({"x": {"v": 60, "n": ""}, "y": {"v": 40, "n": ""}}, {"x": "GOOG", "y": "GOOGL"}) == {"GOOGL": 1.0}


def test_recommend_prefers_steady_and_calm_over_highest_return():
    m = lambda h1, h2, mdd, tot: {"metrics": {"ex_h1": h1, "ex_h2": h2, "mdd": mdd, "spy_mdd": -0.25, "total": tot, "excess": 0.05, "quarters": 20,  # noqa: E731
                                              "cagr": 0.2, "spy_cagr": 0.15, "win": 0.6, "turnover": 0.3}}
    r = st.recommend({"momentum": m(1.2, 0.5, -0.78, 4.6), "blend": m(0.3, 0.25, -0.3, 2.2), "newbuy": m(0.4, -0.03, -0.27, 2.0)})
    assert r["pick"] == "blend" and r["robust"] and r["best_return"] == "momentum"
    r = st.recommend({"momentum": m(0.4, 0.5, -0.3, 4.6), "blend": m(0.3, 0.25, -0.3, 2.2)}, {"momentum": {"min": 0.09, "max": 0.2}})
    assert r["pick"] == "blend" and "momentum" in r["failed"]      # 숫자를 바꾸면 시장에 지는 조합은 제외
    r = st.recommend({"newbuy": m(0.4, -0.03, -0.27, 2.0)})
    assert r["pick"] == "newbuy" and not r["robust"] and r["warnings"]


def test_costs_sizes_changes_and_calculator_fields():
    per, px, today = world()
    px["KRW=X"] = perf.Series(["2026-01-01"], [1400.0])
    out = st.build(per, TK, px, today, {})
    f = out["bases"]["follow"]
    m = f["strategies"]["blend"]["metrics"]
    assert m["total_net"] < m["total"] and m["cagr_net"] < m["cagr"]          # 비용을 빼면 줄어든다
    assert set(f["sizes"]) == {"10", "20", "30"} and f["sizes"]["30"]["cagr"] == m["cagr"]
    assert out["fx"] == {"KRW": 1400.0} and out["cost"] == st.COST
    assert all(h["p"] for h in f["now"]["portfolios"]["blend"])
    ch = f["now"]["changes"]["newbuy"]
    assert ch["prev"] == PERIODS[-2] and "blend10" in f["now"]["portfolios"]
    ports = [{"period": "a", "w": {"x": {"A": .5, "B": .5}}}, {"period": "b", "w": {"x": {"A": .7, "C": .3}}}]
    c = st.changes(ports, "x")
    assert [r["t"] for r in c["in"]] == ["C"] and [r["t"] for r in c["out"]] == ["B"] and [r["t"] for r in c["up"]] == ["A"]


def test_strategy_notification_only_on_new_quarter():
    import notify
    now = {"period": "2026-06-30", "portfolios": {"blend": [{"t": "A", "w": .5}]}, "changes": {"blend": {"in": [{"t": "C"}], "out": [{"t": "B"}]}}}
    new = {"labels": {"blend": "네 가지 혼합"}, "recommend": {"pick": "blend"}, "bases": {"follow": {"now": now}}}
    old = {"bases": {"follow": {"now": {"period": "2026-03-31"}}}}
    lines = notify.strategy_lines(old, new)
    assert "네 가지 혼합" in lines[0] and "C" in lines[1] and "B" in lines[2]
    assert notify.strategy_lines(new, new) == [] and notify.strategy_lines({}, new) == []
    assert notify.notify_strategy(old, new, {"notify": {"telegram": {"enabled": False}}}) == 0


def test_live_track_starts_at_publication_and_adds_a_leg_only_on_change():
    per, px, today = world()
    out = st.build(per, TK, px, today, {})
    d0 = "2026-01-10"
    ledger, live = st.live_track({}, out, px, d0)
    assert live["since"] == d0 and live["days"] == 0 and live["ret"] == 0 and len(ledger["legs"]) == 1
    ledger2, live2 = st.live_track(ledger, out, px, "2026-03-01")           # 추천·분기가 그대로면 구간을 늘리지 않는다
    assert len(ledger2["legs"]) == 1 and live2["days"] == 50 and live2["ret"] != 0 and live2["ret_net"] < live2["ret"]
    first = ledger["legs"][0]["pick"]
    out["recommend"]["pick"] = "momentum" if first != "momentum" else "consensus"
    ledger3, live3 = st.live_track(ledger2, out, px, "2026-03-20")          # 추천이 바뀌면 그날부터 새 구간
    assert [g["pick"] for g in live3["legs"]] == [first, out["recommend"]["pick"]] and live3["legs"][1]["start"] == "2026-03-20"
    assert ledger3["legs"][0]["w"] == ledger["legs"][0]["w"]                 # 지난 구간의 종목·비중은 기록 그대로
    assert st.live_track({}, {"bases": {}}, px, d0) == ({"legs": []}, None)
