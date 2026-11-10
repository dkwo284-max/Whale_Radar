"""마켓 레이더 · ETF 등락 파이프라인 → web/data/etf.json, web/data/etf-series.json

    python scripts/build_etf.py            # 전체 (미국 상장 전 ETF + 중국·인도 현지 목록)
    python scripts/build_etf.py --demo     # 네트워크 없이 예시 데이터 (화면 확인용)
    python scripts/build_etf.py --limit 80 # 미국 상장은 앞 80개만 (점검용, 결과는 out/)

데이터: 나스닥 심볼 목록(nasdaqtraded.txt) + Yahoo Finance(yfinance) 수정종가(배당 반영). 키 불필요.
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
from etf import PERIODS, div_union, drop_stale, exclusion_reason, make_row, market_as_of, rank_union, us_listed_country

SYMDIR = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
EXCH = {"Q": "NASDAQ", "N": "NYSE", "P": "NYSE Arca", "Z": "Cboe BZX", "A": "NYSE American", "V": "IEX"}


def load_cfg() -> dict:
    return yaml.safe_load((ROOT / "etf.yaml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 유니버스

def us_universe(cfg: dict, warnings: list[str]) -> tuple[list[dict], dict]:
    """미국 상장 ETF 목록 → (받을 후보, 제외 집계). 목록을 못 받으면 지난번 캐시."""
    import httpx

    cache_path = CACHE / "etf_universe.json"
    cached = read_json(cache_path, {})
    lines: list[str] = []
    try:
        r = httpx.get(SYMDIR, timeout=60, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; MarketRadar/1.0)"})
        r.raise_for_status()
        lines = r.text.splitlines()
    except Exception as e:  # noqa: BLE001
        warnings.append(f"나스닥 ETF 목록을 받지 못해 지난번 목록을 씁니다 ({type(e).__name__}).")
    listing: dict[str, dict] = {}
    if lines:
        head = lines[0].split("|")
        ix = {k: i for i, k in enumerate(head)}
        for ln in lines[1:]:
            f = ln.split("|")
            if len(f) < len(head) or f[ix["ETF"]] != "Y" or f[ix["Test Issue"]] == "Y":
                continue
            sym = f[ix["Symbol"]]
            if not sym or any(ch in sym for ch in "$^ ") or len(sym) > 6:
                continue
            listing[sym] = {"n": f[ix["Security Name"]].strip(), "x": EXCH.get(f[ix["Listing Exchange"]], f[ix["Listing Exchange"]])}
    else:
        listing = cached.get("listing", {})
    excl = {"leveraged": 0, "inverse": 0, "vix": 0, "manual": 0, "other_country": 0}
    out = []
    manual = set(cfg.get("exclude") or [])
    force_inc = set(cfg.get("force_include") or [])
    force_c = cfg.get("force_country") or {}
    for sym, m in sorted(listing.items()):
        y = sym.replace(".", "-")
        if y in manual:
            excl["manual"] += 1
            continue
        why = None if y in force_inc else exclusion_reason(m["n"])
        if why:
            excl[why] += 1
            continue
        c = force_c.get(y) or us_listed_country(m["n"])
        if c == "other":
            excl["other_country"] += 1
            continue
        out.append({"t": sym, "y": y, "n": m["n"], "en": m["n"], "c": c, "l": "us", "x": m["x"], "cur": "USD"})
    cached["listing"] = listing
    write_json(cache_path, cached)
    return out, excl


def local_universe(cfg: dict) -> list[dict]:
    out = []
    for c, items in (cfg.get("local") or {}).items():
        for it in items:
            y = it["y"]
            suf = y.rsplit(".", 1)[-1]
            x, cur = {"SS": ("상해", "CNY"), "SZ": ("심천", "CNY"), "HK": ("홍콩", "HKD"), "NS": ("NSE", "INR"), "BO": ("BSE", "INR")}.get(suf, (suf, "USD"))
            out.append({"t": y.rsplit(".", 1)[0], "y": y, "n": it["n"], "c": c, "l": "local", "x": x, "cur": cur, "th": it.get("th")})
    return out


# ---------------------------------------------------------------- 가격 (yfinance)

def download(symbols: list[str], start: str, warnings: list[str], chunk: int = 80) -> dict[str, tuple]:
    """{yahoo 티커: (dates, 수정종가(배당 반영), volumes, 원래 종가, [(배당락일, 배당액)])}."""
    import yfinance as yf

    out: dict[str, tuple[list, list, list]] = {}
    for k in range(0, len(symbols), chunk):
        part = symbols[k:k + chunk]
        df = None
        for attempt in range(4):
            try:
                df = yf.download(part, start=start, auto_adjust=False, actions=True, group_by="ticker", threads=True, progress=False, timeout=30)
                break
            except Exception as e:  # noqa: BLE001
                log.warning("download %s.. failed (%s), retry %d", part[0], e, attempt + 1)
                time.sleep(5 * (attempt + 1))
        if df is None or df.empty:
            continue
        for s in part:
            try:
                try:
                    sub = df[s]
                except KeyError:
                    if len(part) > 1:
                        continue
                    sub = df  # 한 종목만 받으면 열이 단일 단계일 수 있음
                sub = sub.dropna(subset=["Close"])
            except Exception:  # noqa: BLE001
                continue
            if sub.empty:
                continue
            ds = [d.strftime("%Y-%m-%d") for d in sub.index]
            raw = [float(x) for x in sub["Close"]]
            adj = [float(x) for x in sub["Adj Close"]] if "Adj Close" in sub else raw
            adj = [a if a == a else r for a, r in zip(adj, raw)]
            divs = []
            if "Dividends" in sub:
                divs = [(d, float(v)) for d, v in zip(ds, sub["Dividends"]) if v == v and v > 0]
            out[s] = (ds, adj, [float(x) if x == x else 0.0 for x in sub["Volume"]], raw, divs)
        time.sleep(1.0)
        log.info("prices %d/%d", min(k + chunk, len(symbols)), len(symbols))
    return out


def fetch_info(sym: str) -> dict:
    """운용사·AUM·보수율·상위 보유종목. 실패한 항목은 비움."""
    import yfinance as yf

    t = yf.Ticker(sym)
    info: dict = {}
    try:
        i = t.info or {}
        info["fam"] = i.get("fundFamily") or ""
        info["aum"] = i.get("totalAssets") or i.get("netAssets")
        er = i.get("netExpenseRatio") if i.get("netExpenseRatio") is not None else i.get("annualReportExpenseRatio")
        info["er"] = er
        info["cat"] = i.get("category") or ""
        info["nm"] = i.get("longName") or i.get("shortName") or ""
        info["inc"] = i.get("fundInceptionDate")
    except Exception:  # noqa: BLE001
        pass
    try:
        th = t.funds_data.top_holdings
        info["hold"] = [[str(idx), str(row.get("Name", "")), round(float(row.get("Holding Percent", 0)) * 100, 2)] for idx, row in th.head(10).iterrows()]
    except Exception:  # noqa: BLE001
        info["hold"] = []
    return info


def normalize_er(er):
    """보수율을 % 로 (yfinance 는 0.09 = 0.09% 또는 0.0009 = 0.09% 를 섞어 준다)."""
    if er is None:
        return None
    try:
        er = float(er)
    except Exception:  # noqa: BLE001
        return None
    return round(er * 100, 3) if er < 0.03 else round(er, 3)


# ---------------------------------------------------------------- 조립

def build(cfg: dict, limit: int = 0) -> tuple[dict, dict]:
    warnings: list[str] = []
    today = date.today()
    start = (today - timedelta(days=420)).isoformat()
    ucache = read_json(CACHE / "etf_universe.json", {})
    skip_until: dict = ucache.get("illiquid_until", {})

    us, excl = us_universe(cfg, warnings)
    if limit:
        us = us[:limit]
    local = local_universe(cfg)
    todo = [m for m in us if skip_until.get(m["y"], "") < today.isoformat()]
    skipped_illiquid = len(us) - len(todo)
    metas = {m["y"]: m for m in todo + local}
    prices = download(list(metas), start, warnings)
    missing_local = [m["y"] for m in local if m["y"] not in prices]
    if missing_local:
        warnings.append("현지 ETF 시세를 못 받은 티커: " + ", ".join(missing_local[:20]))

    min_tv = cfg.get("min_turnover") or {}
    rows, series = [], {}
    excl.update(illiquid=skipped_illiquid, short_history=0, stale=0, small=0)
    glitches: list[str] = []
    recheck = (today + timedelta(days=int(cfg.get("illiquid_recheck_days", 7)))).isoformat()
    for y, (ds, cs, vs, raw, divs) in prices.items():
        m = metas[y]
        made = make_row(m, ds, cs, vs, raw, divs)
        if made == "glitch":
            glitches.append(m["t"])
            continue
        if not made:
            excl["short_history"] += 1
            continue
        row, wk = made
        if row["tv"] < float(min_tv.get(row["cur"], 0)):
            excl["illiquid"] += 1
            if m["l"] == "us":
                skip_until[y] = recheck
            continue
        skip_until.pop(y, None)
        rows.append(row)
        series[y] = wk
    rows, excl["stale"] = drop_stale(rows)
    excl["glitch"] = len(glitches)
    if glitches:
        warnings.append("시세 오류(하루 45% 넘는 변동, 분할 미반영 추정)로 뺀 ETF: " + ", ".join(sorted(glitches)[:30]))

    # 상세정보: 화면에 올라올 후보만, 캐시 우선. AUM 이 작으면 빼고 후보를 다시 뽑는다.
    icache: dict = read_json(CACHE / "etf_info.json", {})
    ttl = (today - timedelta(days=int(cfg.get("info_ttl_days", 14)))).isoformat()
    budget = int(cfg.get("info_max_per_run", 320))
    min_aum = float(cfg.get("min_aum_usd", 0))
    for _ in range(3):
        need = [y for y in sorted(rank_union(rows, int(cfg.get("top_n", 20)) + 5) | div_union(rows)) if icache.get(y, {}).get("at", "") < ttl]
        for y in need[:budget]:
            try:
                icache[y] = {**fetch_info(y), "at": today.isoformat()}
            except Exception as e:  # noqa: BLE001
                log.warning("info %s: %s", y, e)
            time.sleep(0.4)
        budget -= min(budget, len(need))
        before = len(rows)
        kept = []
        for r in rows:
            aum = icache.get(r["y"], {}).get("aum")
            if r["l"] == "us" and aum and aum < min_aum:
                excl["small"] += 1
            else:
                kept.append(r)
        rows = kept
        if len(rows) == before or budget <= 0:
            break

    show = rank_union(rows, int(cfg.get("top_n", 20)) + 5) | div_union(rows)
    info_out = {}
    for y in show:
        i = icache.get(y)
        if not i:
            continue
        info_out[y] = {"fam": i.get("fam") or "", "aum": i.get("aum"), "er": normalize_er(i.get("er")),
                       "cat": i.get("cat") or "", "hold": (i.get("hold") or [])[:10]}
    for r in rows:
        r.pop("en", None)

    write_json(CACHE / "etf_info.json", icache)
    ucache = read_json(CACHE / "etf_universe.json", {})
    ucache["illiquid_until"] = {k: v for k, v in skip_until.items() if v >= today.isoformat()}
    write_json(CACHE / "etf_universe.json", ucache)

    counts = {c: sum(1 for r in rows if r["c"] == c) for c in ("us", "cn", "in")}
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": market_as_of(rows), "periods": PERIODS, "counts": counts, "excluded": excl,
        "rules": {"min_turnover": min_tv, "min_aum_usd": min_aum, "min_history_days": cfg.get("min_history_days", 20)},
        "rows": sorted(rows, key=lambda r: (r["c"], r["l"], r["y"])), "info": info_out, "warnings": warnings,
    }
    return data, {"generated_at": data["generated_at"], "w": series}


# ---------------------------------------------------------------- 예시 데이터

def demo(cfg: dict) -> tuple[dict, dict]:
    rnd = random.Random(7)
    names_us = ["Semiconductor", "Technology Select", "Regional Banks", "Gold Miners", "Uranium", "Clean Energy", "Biotech", "Homebuilders",
                "Bitcoin Trust", "Treasury 20+ Year Bond", "Russell 2000", "S&P 500", "Nasdaq 100", "Aerospace & Defense", "Oil & Gas Exploration",
                "Cybersecurity", "Robotics & AI", "Utilities Select", "Real Estate", "Dividend Growth", "Consumer Discretionary", "Copper Miners",
                "Cloud Computing", "Solar", "Lithium & Battery", "Software", "Insurance", "Healthcare", "Silver", "Equal Weight"]
    rows, series = [], {}
    end = date.today() - timedelta(days=1)
    days = [end - timedelta(days=i) for i in range(400)][::-1]
    days = [d for d in days if d.weekday() < 5]
    spec = []
    for i in range(120):
        nm = names_us[i % len(names_us)]
        spec.append({"t": f"D{i:03d}", "y": f"D{i:03d}", "n": f"Demo {nm} ETF", "en": f"Demo {nm} ETF", "c": "us", "l": "us", "x": "NYSE Arca", "cur": "USD"})
    for i, it in enumerate((cfg.get("local") or {}).get("cn", [])[:30]):
        spec.append({"t": it["y"].split(".")[0], "y": it["y"], "n": it["n"], "c": "cn", "l": "local", "x": "상해", "cur": "CNY", "th": it.get("th")})
    for i, nm in enumerate(["China Large-Cap", "China Internet", "MSCI China", "China Tech", "China Clean Energy", "Hong Kong", "China A-Shares", "China Consumer"]):
        spec.append({"t": f"C{i:02d}", "y": f"C{i:02d}", "n": f"Demo {nm} ETF", "en": f"Demo {nm} ETF", "c": "cn", "l": "us", "x": "NYSE Arca", "cur": "USD"})
    for i, it in enumerate((cfg.get("local") or {}).get("in", [])[:24]):
        spec.append({"t": it["y"].split(".")[0], "y": it["y"], "n": it["n"], "c": "in", "l": "local", "x": "NSE", "cur": "INR", "th": it.get("th")})
    for i, nm in enumerate(["MSCI India", "India Small-Cap", "India Infrastructure", "India Financials", "Nifty 50"]):
        spec.append({"t": f"I{i:02d}", "y": f"I{i:02d}", "n": f"Demo {nm} ETF", "en": f"Demo {nm} ETF", "c": "in", "l": "us", "x": "Cboe BZX", "cur": "USD"})
    info = {}
    for m in spec:
        drift, vol, p = rnd.gauss(0.0004, 0.0008), rnd.uniform(0.006, 0.03), rnd.uniform(15, 300)
        cs = []
        for _ in days:
            p *= 1 + rnd.gauss(drift, vol)
            cs.append(round(p, 2))
        iso = [d.isoformat() for d in days]
        gap = rnd.choice([0, 0, 5, 21, 63, 63, 126, 250])
        y_ = rnd.choice([0.01, 0.02, 0.035, 0.05, 0.08, 0.12, 0.4])
        divs = [(iso[k], round(cs[k] * y_ * gap / 252, 4)) for k in range(len(iso) - 3, 0, -gap)] if gap else []
        made = make_row(m, iso, cs, [1e6] * len(days), cs, divs)
        row, wk = made
        row.pop("en", None)
        rows.append(row)
        series[m["y"]] = wk
        info[m["y"]] = {"fam": "Demo Funds", "aum": rnd.choice([3e8, 1.2e9, 8e9, 4.5e10]), "er": round(rnd.uniform(0.03, 0.75), 2), "cat": "",
                        "hold": [["AAA", "Example Holding A", 8.1], ["BBB", "Example Holding B", 6.4], ["CCC", "Example Holding C", 5.2]]}
    data = {"demo": True, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": market_as_of(rows),
            "periods": PERIODS, "counts": {c: sum(1 for r in rows if r["c"] == c) for c in ("us", "cn", "in")},
            "excluded": {"leveraged": 412, "inverse": 168, "vix": 27, "manual": 27, "other_country": 1030, "illiquid": 1490, "short_history": 61, "stale": 3, "small": 85},
            "rules": {"min_turnover": cfg.get("min_turnover"), "min_aum_usd": cfg.get("min_aum_usd"), "min_history_days": 20},
            "rows": rows, "info": info, "warnings": []}
    return data, {"generated_at": data["generated_at"], "w": series}


def home_subset(data: dict, n: int = 3) -> dict:
    """홈 화면 'ETF 등락 한눈에'에 필요한 것만: 나라별 1일 상승·하락 상위 n 개 (전체 etf.json 은 ETF 탭을 열 때 받는다)."""
    rows = []
    for c in ("us", "cn", "in"):
        pool = [r for r in data.get("rows", []) if r.get("c") == c and r.get("r") and r["r"][0] is not None]
        pick = sorted(pool, key=lambda r: -r["r"][0])[:n] + sorted(pool, key=lambda r: r["r"][0])[:n]
        seen = set()
        for r in pick:
            if r["y"] not in seen:
                seen.add(r["y"])
                rows.append({k: r[k] for k in ("t", "y", "n", "c", "l", "th") if k in r} | {"r": r["r"][:1]})
    return {"generated_at": data.get("generated_at"), "home": True, "rows": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    cfg = load_cfg()
    data, ser = demo(cfg) if a.demo else build(cfg, a.limit)
    out = ROOT / "out" if a.limit else DATA
    if not a.demo and not a.limit and len(data["rows"]) < 100:
        log.error("ETF 가 %d개뿐이라 지난 데이터를 덮어쓰지 않습니다.", len(data["rows"]))
        return 1
    write_json(out / "etf.json", data)
    write_json(out / "etf-series.json", ser)
    write_json(out / "etf-home.json", home_subset(data))
    log.info("etf rows=%d counts=%s excluded=%s", len(data["rows"]), data["counts"], data["excluded"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
