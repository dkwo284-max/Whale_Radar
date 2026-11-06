import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import perf  # noqa: E402


def days(start: str, n: int) -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def test_weights_top_n_merge_and_skip_unknown():
    h = {"A1": {"v": 600}, "A2": {"v": 200}, "B": {"v": 200}, "X": {"v": 100}}
    w = perf.weights(h, {"A1": "AAA", "A2": "AAA", "B": "BBB", "X": None}, n=4)
    assert w == {"AAA": 0.8, "BBB": 0.2}


def test_window_return_contrib_and_coverage():
    ds = days("2025-01-01", 11)
    px = {"AAA": perf.Series(ds, [100 + i * 2 for i in range(11)]),   # +20%
          "BBB": perf.Series(ds, [50 - i for i in range(11)])}        # -20%
    w = perf.window({"AAA": 0.5, "BBB": 0.3, "ZZZ": 0.2}, px, "2025-01-01", "2025-01-11")
    assert abs(w["cov"] - 0.8) < 1e-9                                   # 시세 없는 ZZZ 는 빼고 다시 나눔
    assert abs(w["ret"] - (0.625 * 0.2 + 0.375 * -0.2)) < 1e-9
    assert w["contrib"]["AAA"] > 0 > w["contrib"]["BBB"]
    assert abs(w["vals"][-1] - 1 - w["ret"]) < 1e-9


def test_chain_metrics_mdd():
    a = {"days": ["d1", "d2"], "vals": [1.1, 1.2]}
    b = {"days": ["d3", "d4"], "vals": [0.5, 1.0]}
    d, v = perf.chain([a, b])
    assert d == ["d1", "d2", "d3", "d4"] and [round(x, 3) for x in v] == [1.1, 1.2, 0.6, 1.2]
    assert round(perf.mdd(v), 3) == -0.5
    assert perf.turnover([{"A": 1.0}, {"A": 0.5, "B": 0.5}]) == 0.5


def test_investor_perf_end_to_end():
    ds = days("2024-01-01", 800)
    px = {"AAA": perf.Series(ds, [100 * (1.001 ** i) for i in range(800)]),
          "SPY": perf.Series(ds, [100 * (1.0005 ** i) for i in range(800)])}
    qs = [{"period": "2024-03-31", "filed": "2024-05-15", "total": 100, "h": {"C": {"v": 100, "n": "A"}}},
          {"period": "2024-06-30", "filed": "2024-08-14", "total": 100, "h": {"C": {"v": 100, "n": "A"}}},
          {"period": "2024-09-30", "filed": "2024-11-14", "total": 100, "h": {"C": {"v": 100, "n": "A"}}}]
    out = perf.investor_perf(qs, {"C": "AAA"}, px, ds[-1])
    q = out["quarters"]
    assert len(q) == 3 and q[-1]["partial"] and not q[0]["partial"]
    assert all(r["ret"] > r["spy"] for r in q[:2])                     # 매일 0.1% vs 0.05%
    m = out["metrics"]
    assert m["win"] == 1.0 and m["quarters"] == 2 and m["r1y"] > 0 and m["ex1y"] > 0
    assert out["curve"]["d"] and len(out["curve"]["d"]) == len(out["curve"]["inv"]) == len(out["curve"]["spy"])
    assert out["follow"]["r1y"] is not None
