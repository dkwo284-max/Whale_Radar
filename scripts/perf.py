"""대가별 13F '복제 포트폴리오' 기간별 성적 → web/data/performance.json

13F 는 분기 말 미국 상장 주식 롱 포지션만 담는다(현금·공매도·채권·해외·대부분의 옵션 제외, 분기 중 매매는 안 보임).
그래서 이 값은 대가의 실제 펀드 수익률이 아니라 "13F 보유 종목을 그 비중대로 들고 있었다면"의 추정치다.

- 두 가지 방식
  · 분기말 기준(qe): 각 분기 말(reportDate) 비중으로 다음 분기 말까지 보유
  · 공시일 기준(follow): 공시가 나온 날(filingDate, 최대 45일 뒤)에 따라 사서 다음 공시일까지 보유 — 실제로 따라 할 수 있는 값
- 비중: 평가액 상위 30종목(보통 13F 의 90% 이상)을 다시 100% 로 맞춤. 시세가 없는 종목(상장폐지 등)은 빼고 비중을 다시 나눔(coverage 로 표시)
- 벤치마크: SPY(배당 포함 수정종가)
- AI 를 쓰지 않는다. 과거 13F 는 cache/13f/ 에 저장해 다시 받지 않고, 시세는 yfinance 로 받는다.

    python scripts/perf.py              # 전체 (최근 21개 분기)
    python scripts/perf.py --only buffett
"""
from __future__ import annotations

import argparse
import bisect
import logging
import math
import sys
import time
from datetime import date, timedelta

from common import CACHE, DATA, SEC, load_config, log, read_json, write_json
from meta import Meta
from sec import ARCHIVE, SUBMISSIONS, filings, find_infotable, parse_infotable

QUARTERS = 21       # 5년(20분기) 수익률 + 시작 분기
TOP_N = 30
STALE_DAYS = 200    # 최근 13F 기준일이 이보다 오래되면 '공시 중단'으로 보고 순위에서 뺀다
BENCH = "SPY"
CACHE13F = CACHE / "13f"


# ── 13F 이력 ─────────────────────────────────────────────

def all_13f_rows(sec: SEC, cik: int) -> list[dict]:
    """13F-HR 원본(정정 제외)을 기준 분기마다 하나씩, 최신순. 최근 목록에 부족하면 과거 제출 파일도 읽는다."""
    sub = sec.json(SUBMISSIONS.format(cik=cik))
    rows = filings(sub)
    pick = lambda rs: [r for r in rs if r["form"] == "13F-HR"]  # noqa: E731
    found = pick(rows)
    for f in sub.get("filings", {}).get("files", []):
        if len({r["reportDate"] for r in found}) >= QUARTERS:
            break
        try:
            more = sec.json(f"https://data.sec.gov/submissions/{f['name']}")
            keys = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument")
            found += pick([dict(zip(keys, row)) for row in zip(*(more.get(k, []) for k in keys))])
        except Exception as e:  # noqa: BLE001
            log.warning("older submissions %s failed: %s", f.get("name"), e)
    out, seen = [], set()
    for r in sorted(found, key=lambda r: (r["reportDate"], r["filingDate"]), reverse=True):
        if r["reportDate"] in seen:
            continue
        seen.add(r["reportDate"])
        out.append(r)
    return out[:QUARTERS]


def holdings(sec: SEC, cik: int, row: dict) -> dict | None:
    """한 분기 13F 보유(상위 60종목 평가액)와 총액. cache/13f/{cik}/{accession}.json 에 저장해 다시 받지 않는다."""
    path = CACHE13F / str(cik) / f"{row['accessionNumber']}.json"
    cached = read_json(path, None)
    if cached:
        return cached
    url = find_infotable(sec, cik, row["accessionNumber"])
    if not url:
        return None
    h = parse_infotable(sec.text(url), row["filingDate"])
    total = sum(x["value"] for x in h.values())
    top = sorted(h.items(), key=lambda kv: kv[1]["value"], reverse=True)[:60]
    out = {"period": row["reportDate"], "filed": row["filingDate"], "total": round(total),
           "url": f"{ARCHIVE.format(cik=cik, acc=row['accessionNumber'].replace('-', ''))}/{row['accessionNumber']}-index.htm",
           "h": {c: {"v": round(x["value"]), "n": x["issuer"]} for c, x in top}}
    write_json(path, out)
    return out


# ── 계산 (순수 함수: 테스트 대상) ─────────────────────────

class Series:
    """한 종목의 (날짜 오름차순, 수정종가)."""

    def __init__(self, dates: list[str], px: list[float]):
        self.d, self.p = dates, px

    def at(self, day: str) -> float | None:
        """그날 또는 그 전 마지막 종가 (휴장일 대응). 첫 시세 이전이면 None."""
        i = bisect.bisect_right(self.d, day) - 1
        return self.p[i] if i >= 0 else None

    def between(self, start: str, end: str) -> list[str]:
        return self.d[bisect.bisect_right(self.d, start): bisect.bisect_right(self.d, end)]


def weights(h: dict, tickers: dict[str, str | None], n: int = TOP_N) -> dict[str, float]:
    """상위 n 종목 평가액 → 티커 비중(합 1). 같은 티커(여러 CUSIP)는 합침, 티커 없는 종목은 뺌."""
    w: dict[str, float] = {}
    for c, x in sorted(h.items(), key=lambda kv: kv[1]["v"], reverse=True)[:n]:
        t = tickers.get(c)
        if t:
            w[t] = w.get(t, 0) + x["v"]
    s = sum(w.values())
    return {t: v / s for t, v in w.items()} if s > 0 else {}


def window(w: dict[str, float], px: dict[str, Series], start: str, end: str) -> dict:
    """start 에 비중대로 사서 end 까지 보유. 일별 가치(시작 = 1), 종목별 수익률·기여, 시세가 있던 비중(coverage)."""
    base = {t: px[t].at(start) for t in w if t in px}
    live = {t: wt for t, wt in w.items() if base.get(t)}
    cov = sum(live.values())
    if cov <= 0:
        return {"ret": None, "cov": 0.0, "days": [], "vals": [], "contrib": {}}
    norm = {t: wt / cov for t, wt in live.items()}
    days = sorted({d for t in norm for d in px[t].between(start, end)})
    vals = []
    for d in days:
        vals.append(sum(wt * (px[t].at(d) or base[t]) / base[t] for t, wt in norm.items()))
    end_ret = {t: (px[t].at(end) or base[t]) / base[t] - 1 for t in norm}
    contrib = {t: norm[t] * r for t, r in end_ret.items()}
    ret = sum(contrib.values()) if days else 0.0
    return {"ret": ret, "cov": cov, "days": days, "vals": vals, "contrib": contrib, "rets": end_ret}


def chain(windows: list[dict]) -> tuple[list[str], list[float]]:
    """분기별 일별 가치를 이어 붙인 누적 곡선 (시작 = 1)."""
    days, vals, level = [], [], 1.0
    for wd in windows:
        for d, v in zip(wd["days"], wd["vals"]):
            days.append(d)
            vals.append(level * v)
        if wd["vals"]:
            level *= wd["vals"][-1] if wd["days"] else 1.0
    return days, vals


def ret_since(days: list[str], vals: list[float], since: str) -> float | None:
    """since 시점부터 지금까지 수익률 (곡선이 since 이전부터 있어야 계산)."""
    if not days or days[0] > since:
        return None
    i = bisect.bisect_right(days, since) - 1
    return vals[-1] / vals[i] - 1 if i >= 0 and vals[i] else None


def mdd(vals: list[float]) -> float | None:
    peak, worst = 0.0, 0.0
    for v in vals:
        peak = max(peak, v)
        if peak:
            worst = min(worst, v / peak - 1)
    return worst if vals else None


def vol(vals: list[float]) -> float | None:
    rs = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals)) if vals[i - 1]]
    if len(rs) < 20:
        return None
    m = sum(rs) / len(rs)
    return math.sqrt(sum((r - m) ** 2 for r in rs) / (len(rs) - 1)) * math.sqrt(252)


def turnover(ws: list[dict[str, float]]) -> float | None:
    """분기 평균 회전율 = ½·Σ|비중 변화| (0 = 그대로, 1 = 전부 교체)."""
    vs = []
    for a, b in zip(ws, ws[1:]):
        keys = set(a) | set(b)
        vs.append(0.5 * sum(abs(a.get(k, 0) - b.get(k, 0)) for k in keys))
    return sum(vs) / len(vs) if vs else None


def sample(days: list[str], vals: list[float], step: int = 5) -> tuple[list[str], list[float]]:
    """차트용으로 일주일 간격만 (마지막 날은 항상 포함)."""
    idx = list(range(0, len(days), step))
    if days and idx[-1] != len(days) - 1:
        idx.append(len(days) - 1)
    return [days[i] for i in idx], [round(vals[i], 4) for i in idx]


def metrics(days: list[str], vals: list[float], today: str) -> dict:
    t = date.fromisoformat(today)
    since = lambda years: (t - timedelta(days=round(365.25 * years))).isoformat()  # noqa: E731
    out = {}
    for k, y in (("1y", 1), ("3y", 3), ("5y", 5)):
        r = ret_since(days, vals, since(y))
        out[f"r{k}"] = r
        out[f"cagr{k}"] = ((1 + r) ** (1 / y) - 1) if r is not None and r > -1 else None
    out["mdd"] = mdd(vals)
    out["vol"] = vol(vals)
    return out


def investor_perf(qs: list[dict], tickers: dict[str, str | None], px: dict[str, Series], today: str) -> dict:
    """qs: 분기 13F (오래된 순). 분기별 성적·누적 곡선·지표."""
    ws = [weights(q["h"], tickers) for q in qs]
    # 최근 13F 가 오래됐으면(공시 중단·제출 대상 아님) 마지막 분기를 지금까지 늘리지 않고 다음 분기 말(약 3개월)에서 끊는다
    last_p = qs[-1]["period"] if qs else today
    stale = (date.fromisoformat(today) - date.fromisoformat(last_p)).days > STALE_DAYS
    cap = lambda d: min(today, (date.fromisoformat(d) + timedelta(days=92)).isoformat()) if stale else today  # noqa: E731
    bench = px.get(BENCH)
    rows, qe_w, fo_w, bq_w = [], [], [], []
    for i, q in enumerate(qs):
        nxt = qs[i + 1] if i + 1 < len(qs) else None
        end_qe = nxt["period"] if nxt else cap(q["period"])
        end_fo = nxt["filed"] if nxt else cap(q["filed"])
        a = window(ws[i], px, q["period"], end_qe)
        b = window(ws[i], px, q["filed"], end_fo)
        bq = window({BENCH: 1.0}, px, q["period"], end_qe) if bench else None
        bf = window({BENCH: 1.0}, px, q["filed"], end_fo) if bench else None
        qe_w.append(a)
        fo_w.append(b)
        if bq:
            bq_w.append(bq)
        ranked = sorted(a["contrib"].items(), key=lambda kv: kv[1], reverse=True)
        top10 = sum(x["v"] for x in sorted(q["h"].values(), key=lambda x: x["v"], reverse=True)[:10])
        rows.append({
            "period": q["period"], "filed": q["filed"], "url": q.get("url", ""), "partial": nxt is None and not stale,
            "ret": a["ret"], "spy": bq["ret"] if bq else None, "ret_follow": b["ret"], "spy_follow": bf["ret"] if bf else None,
            "cov": round(a["cov"], 3), "top10": round(top10 / q["total"], 3) if q.get("total") else None,
            "best": [{"t": t, "c": round(c, 4), "r": round(a["rets"][t], 4)} for t, c in ranked[:3] if c > 0],
            "worst": [{"t": t, "c": round(c, 4), "r": round(a["rets"][t], 4)} for t, c in ranked[::-1][:3] if c < 0],
        })
    d_qe, v_qe = chain(qe_w)
    d_fo, v_fo = chain(fo_w)
    d_sp, v_sp = chain(bq_w)
    until = d_qe[-1] if d_qe else today  # 지표 기준일 (공시가 끊긴 투자자는 마지막 13F 다음 분기 말)
    m = metrics(d_qe, v_qe, until)
    mf = metrics(d_fo, v_fo, d_fo[-1] if d_fo else until)
    ms = metrics(d_sp, v_sp, until)
    done = [r for r in rows if not r["partial"] and r["ret"] is not None and r["spy"] is not None]
    m["win"] = (sum(1 for r in done if r["ret"] > r["spy"]) / len(done)) if done else None
    m["quarters"] = len(done)
    m["turnover"] = turnover(ws)
    m["top10"] = rows[-1]["top10"] if rows else None
    for k in ("r1y", "r3y", "r5y"):
        m[f"ex{k[1:]}"] = (m[k] - ms[k]) if m.get(k) is not None and ms.get(k) is not None else None
    cd, cv = sample(d_qe, v_qe)
    fd, fv = sample(d_fo, v_fo) if d_fo else ([], [])
    sd = dict(zip(d_sp, v_sp))
    return {"stale": stale, "until": until, "quarters": rows, "metrics": m, "follow": mf, "spy": ms,
            "curve": {"d": cd, "inv": cv, "spy": [round(sd[d], 4) if d in sd else None for d in cd], "fd": fd, "follow": fv}}


# ── 시세 ────────────────────────────────────────────────

def yf_symbol(t: str) -> str:
    return t.replace(".", "-").replace("/", "-")


def download(tickers: list[str], start: str) -> dict[str, Series]:
    import yfinance as yf

    out: dict[str, Series] = {}
    syms = {yf_symbol(t): t for t in tickers}
    names = list(syms)
    for k in range(0, len(names), 80):
        part = names[k:k + 80]
        df = None
        for attempt in range(3):
            try:
                df = yf.download(part, start=start, auto_adjust=True, group_by="ticker", threads=True, progress=False, timeout=30)
                break
            except Exception as e:  # noqa: BLE001
                log.warning("prices %s.. failed (%s), retry %d", part[0], e, attempt + 1)
                time.sleep(5 * (attempt + 1))
        if df is None or df.empty:
            continue
        for s in part:
            try:
                sub = df[s] if len(part) > 1 else df
                close = sub["Close"].dropna()
            except Exception:  # noqa: BLE001
                continue
            if close.empty:
                continue
            out[syms[s]] = Series([d.strftime("%Y-%m-%d") for d in close.index], [float(x) for x in close])
        log.info("prices %d/%d", min(k + 80, len(names)), len(names))
        time.sleep(1.0)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = load_config()
    invs = cfg["investors"]
    if args.only:
        keep = set(args.only.split(","))
        invs = [i for i in invs if i["id"] in keep]
    sec = SEC(cfg)
    meta = Meta(sec)
    per: dict[str, list[dict]] = {}
    for inv in invs:
        try:
            rows = all_13f_rows(sec, inv["cik"])
            qs = [q for q in (holdings(sec, inv["cik"], r) for r in rows) if q and q["h"]]
            per[inv["id"]] = sorted(qs, key=lambda q: q["period"])
            log.info("%-14s %d quarters (%s ~ %s)", inv["id"], len(qs), qs[-1]["period"] if qs else "-", qs[0]["period"] if qs else "-")
        except Exception as e:  # noqa: BLE001
            log.warning("13F history %s failed: %s", inv["id"], e)
    cusips = {c for qs in per.values() for q in qs for c in sorted(q["h"], key=lambda c: q["h"][c]["v"], reverse=True)[:TOP_N]}
    tickers = meta.tickers_for(cusips)
    need = sorted({t for t in tickers.values() if t} | {BENCH})
    first = min((q["period"] for qs in per.values() for q in qs), default=date.today().isoformat())
    start = (date.fromisoformat(first) - timedelta(days=10)).isoformat()
    log.info("prices: %d tickers since %s", len(need), start)
    px = download(need, start)
    if BENCH not in px:
        log.error("benchmark %s prices missing — not writing", BENCH)
        return 1
    today = px[BENCH].d[-1]
    out = {"generated_at": date.today().isoformat(), "as_of": today, "benchmark": BENCH, "top_n": TOP_N, "investors": []}
    old = read_json(DATA / "performance.json", {}) if args.only else {}
    for inv in invs:
        qs = per.get(inv["id"])
        if not qs:
            continue
        p = investor_perf(qs, tickers, px, today)
        out["investors"].append({"id": inv["id"], "name": inv["name"], "firm": inv["firm"], **p})
    if args.only:
        mine = {i["id"] for i in out["investors"]}
        out["investors"] += [i for i in old.get("investors", []) if i["id"] not in mine]
    write_json(DATA / "performance.json", out)
    log.info("performance: %d investors, as of %s", len(out["investors"]), today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
