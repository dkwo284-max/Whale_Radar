"""데이터 상태(status.json): 화면의 '데이터 상태' 패널이 읽는다. 각 수집(공시·뉴스·성적·ETF·글로벌·해설)의 마지막 갱신 시각만 모은다. AI 없음."""
from __future__ import annotations

from datetime import datetime, timezone

from common import DATA, ROOT, read_json, write_json


def build_status() -> dict:
    dash, news, perf = read_json(DATA / "dashboard.json", {}), read_json(DATA / "news.json", {}), read_json(DATA / "performance.json", {})
    etf, macro, usage = read_json(DATA / "etf.json", {}), read_json(DATA / "macro.json", {}), read_json(DATA / "ai-usage.json", {})
    notes = ROOT / "web" / "notes"
    en, mn = read_json(notes / "etf-notes.json", {}), read_json(notes / "macro-notes.json", {})
    inv = dash.get("investors", [])
    stamp = lambda d: d.get("generated_at") or d.get("as_of") or d.get("date") or d.get("updated")  # noqa: E731
    days = usage.get("days", {})
    last_day = max(days) if days else None
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "filings": {"at": stamp(dash), "investors": len(inv), "stale": [i["id"] for i in inv if i.get("stale")], "warnings": dash.get("warnings", [])},
        "news": {"at": stamp(news), "items": len(news.get("items", [])), "no_ai": sum(1 for x in news.get("items", []) if not x.get("ai")), "warnings": news.get("warnings", [])},
        "perf": {"at": stamp(perf), "investors": len(perf.get("investors", []))},
        "etf": {"at": stamp(etf)}, "macro": {"at": stamp(macro)},
        "etf_notes": {"at": stamp(en)}, "macro_notes": {"at": stamp(mn)},
        "ai": {"day": last_day, "requests": (days.get(last_day) or {}).get("requests", 0) if last_day else 0, "cap": usage.get("cap")},
    }


def write_status() -> None:
    try:
        write_json(DATA / "status.json", build_status())
    except Exception:  # noqa: BLE001 — 상태 기록 실패가 수집을 막지 않게
        pass


if __name__ == "__main__":
    write_status()
