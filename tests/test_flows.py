import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from flows import build_flows  # noqa: E402


def pos(t, sec, v, pv, flow, label):
    return {"ticker": t, "issuer": t, "sector": sec, "value": v, "prev_value": pv, "flow": flow, "label": label}


def test_build_flows_tags_and_consensus():
    dash = {"generated_at": "x", "timeline": [], "consensus": [
        {"ticker": "AAA", "issuer": "A", "sector": "IT", "buyers": [{"investor": "a"}, {"investor": "b"}, {"investor": "c"}], "sellers": []},
        {"ticker": "BBB", "issuer": "B", "sector": "IT", "buyers": [{"investor": "a"}, {"investor": "b"}], "sellers": [{"investor": "c"}, {"investor": "d"}]}],
        "investors": [
            {"id": "a", "name": "A", "period": "2026-09-30", "total_value": 200, "positions": [pos("X", "IT", 150, 50, 90, "비중 확대"), pos("Y", "금융", 50, 50, 0, "유지")]},
            {"id": "b", "name": "B", "period": "2026-09-30", "total_value": 60, "positions": [pos("X", "IT", 0, 50, -50, "전량 매도"), pos("Z", "금융", 60, 50, 0, "유지")]},
            {"id": "c", "name": "C", "period": "2026-09-30", "total_value": 100, "positions": [pos("Q", "IT", 100, 100, 1, "유지")]},
        ]}
    f = build_flows(dash, {"items": []}, None, date(2026, 11, 21))
    tags = {r["id"]: r["tag"] for r in f["investors"]}
    assert tags == {"a": "적극 매수", "b": "비중 축소", "c": "관망"}
    assert [x["t"] for x in f["agree_buy"]] == ["AAA"] and [x["t"] for x in f["split"]] == ["BBB"]
    a = next(r for r in f["investors"] if r["id"] == "a")
    assert a["buys"][0]["t"] == "X" and a["net"] == 90 and "IT" in a["line"]
    assert f["counts"]["n"] == 3 and "3분기" in f["lead"]
