"""마켓 레이더 · 글로벌(패권 이동) 파이프라인 → web/data/macro.json

    python scripts/build_macro.py          # 야후 파이낸스에서 나라·환율·원자재 시세를 받아 계산
    python scripts/build_macro.py --demo   # 네트워크 없이 예시 데이터(화면 확인용)

입력: macro.yaml (대상 목록), web/data/hegemony.json (패권 레이더 지역 신호), web/data/dashboard.json (13F 큰손)
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from datetime import date, datetime, timedelta, timezone

import yaml

from common import CACHE, DATA, ROOT, log, read_json, write_json
from etf import avg_turnover, fix_splits, has_glitch
from macro import (I1Y, I90, MP, changes, flow_ratio, hegemony_regions, quadrant, rank_candidates, rel, rel_at, rets, rs_weekly,
                   score_candidate, score_history, smart_money, to_nzd, vol_dd)

KIND_ORDER = {"base": 0, "region": 1, "country": 2, "bloc": 3, "theme": 4, "global": 5}


def load_cfg() -> dict:
    return yaml.safe_load((ROOT / "macro.yaml").read_text(encoding="utf-8"))


def pool(cfg: dict) -> dict[str, dict]:
    """대상 티커 → 메타. 같은 티커가 여러 곳에 나오면 앞선 종류를 대표로 쓴다."""
    names = cfg.get("names") or {}
    out: dict[str, dict] = {}

    def add(y, n, kind, rg, **kw):
        cur = out.get(y)
        if cur is None:
            out[y] = {"y": y, "n": names.get(y, n), "k": kind, "rg": rg, **kw}
            return
        if KIND_ORDER[kind] < KIND_ORDER[cur["k"]]:
            cur["k"] = kind
        for k, v in kw.items():
            cur.setdefault(k, v)

    add(cfg["base"], names.get(cfg["base"], "미국 S&P 500"), "base", "us")
    for rid, r in cfg["regions"].items():
        add(r["etf"], names.get(r["etf"], r["n"]), "region", rid)
    for c in cfg["countries"]:
        add(c["y"], c["n"], "country", c["rg"], cur=c.get("cur"), ctry=True)
    for b in cfg.get("blocs") or []:
        add(b["y"], b["n"], "bloc", b["rg"])
    for t in cfg["themes"]:
        for rg, y in (t.get("cells") or {}).items():
            add(y, y, "theme", rg)
            out[y].setdefault("th", [])
            if t["id"] not in out[y]["th"]:
                out[y]["th"].append(t["id"])
    for g in cfg.get("globals") or []:
        add(g["y"], g["n"], "global", "gl")
    return out


def download_all(cfg: dict, symbols: list[str], warnings: list[str]) -> dict:
    from build_etf import download
    start = (date.today() - timedelta(days=430)).isoformat()
    return download(symbols, start, warnings, chunk=60)


def info_for(syms: list[str], today: date, ttl_days: int) -> dict:
    """상위 보유종목(집중도 점검용). 캐시 우선, 실패하면 비움."""
    from build_etf import fetch_info
    path = CACHE / "macro_info.json"
    cache = read_json(path, {})
    ttl = (today - timedelta(days=ttl_days)).isoformat()
    for y in syms:
        if cache.get(y, {}).get("at", "") >= ttl:
            continue
        try:
            i = fetch_info(y)
            cache[y] = {"hold": (i.get("hold") or [])[:10], "aum": i.get("aum"), "at": today.isoformat()}
        except Exception as e:  # noqa: BLE001
            log.warning("info %s: %s", y, e)
        time.sleep(0.4)
    write_json(path, cache)
    return cache


def compute(cfg: dict, prices: dict, heg: dict, dash: dict, info: dict, warnings: list[str]) -> dict:
    """시세·신호 → macro.json. 네트워크를 쓰지 않는다."""
    base = cfg["base"]
    meta = pool(cfg)
    if base not in prices:
        raise RuntimeError(f"기준 {base} 시세가 없어 계산할 수 없습니다.")
    bd, bc, bv, *_ = prices[base]
    fxb = prices.get(cfg["fx_base"])
    regs = hegemony_regions(heg)
    hist = score_history(heg)
    sm = smart_money(dash, cfg)

    # 환율: '외화 가치(달러 대비)' 시계열
    fx_out, fx_by_cur = [], {}
    for f in cfg.get("fx") or []:
        p = prices.get(f["y"])
        if not p:
            warnings.append(f"환율 {f['y']} 시세 없음")
            continue
        ds, cs = p[0], [(1 / c if f.get("inv") else c) for c in p[1]]
        r = rets(ds, cs)
        fx_by_cur[f["k"]] = r[I1Y]
        fx_out.append({"k": f["k"], "n": f["n"], "y": f["y"], "r": r, "rs": rs_weekly(ds, cs, ds, [1.0] * len(ds), 53)})
    mx_out = []
    for m in cfg.get("macro") or []:
        p = prices.get(m["y"])
        if not p:
            warnings.append(f"지표 {m['y']} 시세 없음")
            continue
        ds, cs = p[0], list(p[1])
        if m.get("u") == "pct" and cs[-1] > 20:   # 야후가 금리를 ×10 으로 줄 때가 있다(42.5 = 4.25%)
            cs = [c / 10 for c in cs]
        mx_out.append({"y": m["y"], "n": m["n"], "u": m.get("u"), "v": round(cs[-1], 2),
                       "r": changes(ds, cs) if m.get("diff") else rets(ds, cs), "diff": bool(m.get("diff")),
                       "rs": rs_weekly(ds, cs, ds, [1.0] * len(ds), 53)})

    items: dict[str, dict] = {}
    for y, m in meta.items():
        p = prices.get(y)
        if not p:
            warnings.append(f"{y} 시세 없음")
            continue
        ds, cs, vs, raw, _ = p
        cs, vs = fix_splits(cs, vs)
        if has_glitch(cs):
            warnings.append(f"{y} 시세 오류(하루 45% 넘는 변동)로 뺌")
            continue
        r = rets(ds, cs)
        if r[I90] is None and r[I1Y] is None:
            warnings.append(f"{y} 이력이 짧아 뺌")
            continue
        it = {k: v for k, v in m.items() if v is not None}
        it["r"] = r
        if fxb:
            nd, nc = to_nzd(ds, cs, fxb[0], fxb[1])
            it["rn"] = rets(nd, nc) if len(nd) > 30 else [None] * len(MP)
        it["rl"] = rel(r, rets(bd, bc)) if y != base else [0.0 if x is not None else None for x in r]
        it["rs"] = rs_weekly(ds, cs, bd, bc) if y != base else []
        it["rp"] = rel_at(ds, cs, bd, bc, 28) if y != base else 0.0
        it["vol"], it["dd"] = vol_dd(cs)
        it["to"] = round(avg_turnover(cs, vs))
        it["fr"] = flow_ratio(cs, vs)
        if m.get("cur"):
            it["fx1y"] = fx_by_cur.get(m["cur"])
        h = (info.get(y) or {}).get("hold") or []
        if h:
            it["hold"] = h[:5]
        it["d"] = ds[-1]
        it["rn_ko"] = (cfg["regions"].get(m["rg"]) or {}).get("n", "")
        items[y] = it

    # 후보 점수
    cands = []
    for y, it in items.items():
        if it["k"] in ("base", "bloc") and y not in {r["etf"] for r in cfg["regions"].values()}:
            continue
        if y == base:
            continue
        c = score_candidate(it, regs, sm, cfg)
        c["r90"] = it["rl"][I90]
        c["r"] = it["r"]
        cands.append(c)
    cands = rank_candidates(cands)

    # 지역 요약(신호 × 시장 반응)
    regions = {}
    for rid, r in cfg["regions"].items():
        etf = items.get(r["etf"])
        h = regs.get(rid)
        score = h["score"] if h and len(h["unknown"]) < 5 else None
        r90 = etf["rl"][I90] if etf else None
        hs = hist.get(rid) or []
        prev = hs[-2]["score"] if len(hs) >= 2 else None
        regions[rid] = {
            "n": r["n"], "etf": r["etf"], "score": score, "prev": prev, "prev_date": hs[-2]["date"] if len(hs) >= 2 else None,
            "ups": h["ups"] if h else [], "downs": h["downs"] if h else [], "unknown": h["unknown"] if h else [],
            "r90": r90, "rp": etf.get("rp") if etf else None, "q": quadrant(score, r90) if rid != "us" else None,
            "sm": {k: sm.get(rid, {}).get(k) for k in ("buy", "sell", "buys", "sells")} if rid in sm else None,
        }

    themes = [{"id": t["id"], "n": t["n"], "why": t.get("why", ""), "cells": {rg: y for rg, y in (t.get("cells") or {}).items() if y in items}}
              for t in cfg["themes"]]
    for it in items.values():
        it.pop("rn_ko", None)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": bd[-1], "periods": MP, "base": base,
        "heg_asof": heg.get("asof"), "items": sorted(items.values(), key=lambda x: (KIND_ORDER[x["k"]], x["y"])),
        "cands": cands, "regions": regions, "themes": themes, "fx": fx_out, "mx": mx_out,
        "sm": {k: v for k, v in sm.items() if k != "_t"}, "warnings": warnings,
    }


# ---------------------------------------------------------------- 예시 데이터

def demo_prices(cfg: dict) -> dict:
    rnd = random.Random(7)
    meta = pool(cfg)
    syms = set(meta) | {f["y"] for f in cfg.get("fx") or []} | {m["y"] for m in cfg.get("macro") or []} | {cfg["fx_base"]}
    drift = {"eu": 0.0004, "cn": 0.0006, "in": -0.0003, "la": -0.0002, "ot": 0.0002, "us": 0.0003, "gl": 0.0005}
    end = date.today()
    days = [end - timedelta(days=i) for i in range(430, -1, -1) if (end - timedelta(days=i)).weekday() < 5]
    ds = [d.isoformat() for d in days]
    out = {}
    for y in sorted(syms):
        rg = (meta.get(y) or {}).get("rg", "us")
        mu = drift.get(rg, 0.0003) + rnd.uniform(-0.0003, 0.0003)
        if y.endswith("=X") or y.endswith(".NYB"):
            mu = rnd.uniform(-0.0002, 0.0002)
        p, cs = 100.0 if not y.endswith("=X") else rnd.uniform(0.6, 1.2), []
        for _ in ds:
            p *= 1 + mu + rnd.gauss(0, 0.009 if "=" not in y else 0.003)
            cs.append(p)
        out[y] = (ds, cs, [rnd.randint(8, 30) * 100_000] * len(ds), cs, [])
    return out


# ---------------------------------------------------------------- 실행

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    cfg = load_cfg()
    heg = read_json(DATA / "hegemony.json", {})
    dash = read_json(DATA / "dashboard.json", {})
    warnings: list[str] = []
    today = date.today()
    if a.demo:
        prices, info = demo_prices(cfg), {}
    else:
        syms = sorted(set(pool(cfg)) | {f["y"] for f in cfg.get("fx") or []} | {m["y"] for m in cfg.get("macro") or []} | {cfg["fx_base"]})
        prices = download_all(cfg, syms, warnings)
        etfs = [y for y, m in pool(cfg).items() if m["k"] != "base" and y in prices and y not in ("ASML",)]
        info = info_for(etfs, today, int((cfg.get("thresholds") or {}).get("info_ttl_days", 14)))
    try:
        data = compute(cfg, prices, heg, dash, info, warnings)
    except RuntimeError as e:
        log.error("%s", e)
        return 1
    data["demo"] = a.demo
    if not a.demo and len(data["items"]) < 30:
        log.error("종목이 %d개뿐이라 지난 데이터를 덮어쓰지 않습니다.", len(data["items"]))
        return 1
    write_json(DATA / "macro.json", data)
    log.info("macro items=%d cands=%d (후보 %d · 관찰 %d) warnings=%d", len(data["items"]), len(data["cands"]),
             sum(c["status"] == "cand" for c in data["cands"]), sum(c["status"] == "watch" for c in data["cands"]), len(warnings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
