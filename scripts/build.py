"""Whale Radar 파이프라인: SEC 공시 + 뉴스 → web/data/dashboard.json, web/data/news.json

    python scripts/build.py              # 전체 (SEC + 뉴스 + AI 요약)
    python scripts/build.py --no-news    # SEC 공시만
    python scripts/build.py --only buffett,ackman   # 일부 투자자만 (점검용, 결과는 out/ 에)
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from common import DATA, ROOT, SEC, load_config, log, read_json, write_json
from meta import Meta
from sec import BROWSE, SUBMISSIONS, compare, events, filings, latest_13f


def investor_block(sec: SEC, inv: dict, cfg: dict, warnings: list[str]) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """투자자 한 명: (프로필, 13F 비교 결과, 13F 타임라인 항목, 제출 목록)."""
    scfg = cfg["sec"]
    sub = sec.json(SUBMISSIONS.format(cik=inv["cik"]))
    if inv.get("expect") and inv["expect"].lower() not in sub.get("name", "").lower():
        warnings.append(f"{inv['name']}: SEC 등록 이름이 '{sub.get('name')}'로 설정(CIK {inv['cik']})과 다를 수 있습니다.")
    rows = filings(sub)
    f13 = latest_13f(sec, inv["cik"], rows)
    block = {"id": inv["id"], "name": inv["name"], "firm": inv["firm"], "cik": inv["cik"], "positions": [],
             "period": None, "prev_period": None, "filed": None,
             "filing_url": BROWSE.format(cik=inv["cik"], form="13F-HR"), "total_value": 0, "stale": True,
             "counts": {"BUY": 0, "HOLD": 0, "SELL": 0}}
    timeline: list[dict] = []
    positions: list[dict] = []
    if f13:
        cur = f13[0]
        prev = f13[1] if len(f13) > 1 else None
        positions = compare(cur["holdings"], prev["holdings"] if prev else {}, float(scfg.get("threshold_pct", 5)))
        if not prev:  # 비교할 직전 분기가 없으면 모두 '유지'로
            for p in positions:
                p.update(signal="HOLD", label="유지", change_pct=None, flow=0)
            warnings.append(f"{inv['name']}: 직전 분기 13F 가 없어 변화 비교를 못 했습니다.")
        stale_cut = (date.today() - timedelta(days=int(scfg.get("stale_days", 200)))).isoformat()
        block.update(period=cur["reportDate"], prev_period=prev["reportDate"] if prev else None, filed=cur["filingDate"],
                     filing_url=cur["url"], total_value=round(sum(h["value"] for h in cur["holdings"].values())),
                     stale=cur["reportDate"] < stale_cut)
        buys = sum(1 for p in positions if p["signal"] == "BUY")
        sells = sum(1 for p in positions if p["signal"] == "SELL")
        timeline.append({"date": cur["filingDate"], "investor": inv["id"], "form": "13F-HR",
                         "title": f"{cur['reportDate'][:7]} 분기 보유종목 공시", "signal": None, "ticker": None, "issuer": None,
                         "detail": f"신규·확대 {buys} · 축소·매도 {sells}", "url": cur["url"]})
    else:
        warnings.append(f"{inv['name']}: 최근 13F 공시를 찾지 못했습니다.")
    return block, positions, timeline, rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg = load_config()
    if args.only:
        keep = set(args.only.split(","))
        cfg["investors"] = [i for i in cfg["investors"] if i["id"] in keep]
    out_dir = ROOT / "out" if args.only else DATA
    scfg = cfg["sec"]
    sec = SEC(cfg)
    meta = Meta(sec)
    warnings: list[str] = []
    investors, all_pos, timeline = [], {}, []

    cik_ticker = meta.cik_to_ticker()
    for inv in cfg["investors"]:
        try:
            block, positions, tl, sub_rows = investor_block(sec, inv, cfg, warnings)
            evs = events(sec, inv["cik"], sub_rows, int(scfg.get("timeline_days", 180)), int(scfg.get("max_event_filings", 12)), cik_ticker)
            tl += [{"investor": inv["id"], **e} for e in evs]
        except Exception as e:  # noqa: BLE001 — 한 명 실패해도 나머지는 진행
            log.error("investor %s failed: %s", inv["id"], e)
            warnings.append(f"{inv['name']}: 공시를 읽지 못했습니다 ({str(e)[:80]}).")
            continue
        investors.append(block)
        all_pos[inv["id"]] = positions
        timeline += tl
        log.info("%-14s period=%s positions=%d events=%d", inv["id"], block["period"], len(positions), len(tl))

    # 티커·섹터
    cusips = {p["cusip"] for ps in all_pos.values() for p in ps}
    tickers = meta.tickers_for(cusips)
    top_n = int(scfg.get("top_positions", 50))
    for block in investors:
        ps = all_pos[block["id"]]
        for p in ps:
            p["ticker"] = tickers.get(p["cusip"])
            p["sector"] = meta.sector_for(p["ticker"], p["issuer"])
        block["counts"] = {s: sum(1 for p in ps if p["signal"] == s) for s in ("BUY", "HOLD", "SELL")}
        ps.sort(key=lambda p: max(p["value"], p["prev_value"]), reverse=True)
        block["positions"] = sorted(ps[:top_n], key=lambda p: (p["value"] == 0, -p["value"], -p["prev_value"]))
    meta.save()

    # 컨센서스·섹터 흐름 (최근 13F 가 있는 투자자만)
    live = [b for b in investors if not b["stale"]]
    by_tk: dict[str, dict] = {}
    sectors: dict[str, dict] = defaultdict(lambda: {"buy_flow": 0, "sell_flow": 0, "BUY": 0, "SELL": 0, "HOLD": 0})
    for b in live:
        for p in b["positions"]:
            key = p["issuer"].lower() or p["ticker"] or p["cusip"]  # 같은 회사의 여러 주식(GOOG·GOOGL)은 한 줄로
            o = by_tk.setdefault(key, {"ticker": p["ticker"], "issuer": p["issuer"], "sector": p["sector"],
                                       "buyers": [], "sellers": [], "holders": [], "flow": 0})
            entry = {"investor": b["id"], "label": p["label"], "change_pct": p["change_pct"]}
            o["flow"] += p["flow"]
            # 한 투자자가 같은 회사의 여러 주식(GOOG·GOOGL)을 가져도 한 번만 센다 (평가액 큰 쪽 기준 — 정렬돼 있음)
            if not any(e["investor"] == b["id"] for k in ("buyers", "sellers", "holders") for e in o[k]):
                o[{"BUY": "buyers", "SELL": "sellers", "HOLD": "holders"}[p["signal"]]].append(entry)
            s = sectors[p["sector"] or "ETF·기타"]
            s[p["signal"]] += 1
            if p["flow"] > 0:
                s["buy_flow"] += p["flow"]
            elif p["flow"] < 0:
                s["sell_flow"] += -p["flow"]
    consensus = sorted((o for o in by_tk.values() if len(o["buyers"]) + len(o["sellers"]) >= 2),
                       key=lambda o: (len(o["buyers"]) + len(o["sellers"]), abs(o["flow"])), reverse=True)[:40]

    timeline.sort(key=lambda t: (t["date"], t["investor"]), reverse=True)
    dashboard = {"demo": False, "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                 "threshold_pct": float(scfg.get("threshold_pct", 5)), "investors": investors, "timeline": timeline[:400],
                 "consensus": consensus, "sectors": dict(sectors), "warnings": warnings}
    if not investors:
        log.error("no investor data — not writing")
        return 1
    write_json(out_dir / "dashboard.json", dashboard)

    if not args.no_news:
        from llm import Gemini
        from news import update_news

        from budget import allowance, describe, nz_today
        from usage import record, used_today

        today = nz_today()  # 하루 = 오클랜드 00시 리셋 (세 앱 공통)
        llm = Gemini(cfg)
        lc = cfg.get("llm", {})
        left, binfo = allowance("whale-radar", used_today(out_dir, today), int(lc.get("guaranteed", 3)), today)
        log.info("%s", describe("whale-radar", left, binfo))
        if left < int(lc.get("expected_per_run", 1)) and llm.enabled:
            log.warning("AI 예산 부족: 오늘 남은 %d회 — 이번 실행은 뉴스 AI 요약을 건너뜀", left)
        else:
            llm.max_requests = min(llm.max_requests, max(left, 0))
            news = update_news(cfg, read_json(DATA / "news.json", {}) if not args.only else {}, llm)
            write_json(out_dir / "news.json", news)
            if llm.requests:
                record(out_dir, today, llm.requests, cfg)
    log.info("done: %d investors, %d timeline, %d consensus", len(investors), len(timeline), len(consensus))
    return 0


if __name__ == "__main__":
    sys.exit(main())
