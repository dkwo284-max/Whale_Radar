"""신규 편입 추천: '이미 들고 있는 종목'이 아니라 '새로 산 종목' 중 누구 것을 따라갈지 과거 성적으로 고른다. AI 를 쓰지 않는다.

① 대가별 신규 편입 성적표: 과거 분기마다 새로 산 종목(비중 1% 이상)이 '공시일' 이후 3·6·12개월 동안 S&P500 대비 얼마나 올랐나
② 이번 분기 신규 편입 추천 목록: 성적표가 좋은 대가가 새로 산 종목 — 대가 성적·편입 비중·동시 매수 대가 수로 점수
③ 공시 지연 보정: 대가가 산 분기의 평균가·공시일 종가와 지금 가격 비교 (이미 많이 오른 종목 표시)
④ 유형 구분: 완전 신규(new) / 재진입(reentry) / 소량 보유 후 대폭 확대(scaleup) — 유형별 과거 성적
⑤ 상위 종목을 같은 비중으로 담는 조합의 실제 추적(strategy.live_track 장부 재사용)

주의: 대가 한 명의 표본은 수십 건이라 우연의 영향이 크다 → 표본 수와 95% 신뢰 구간을 함께 내고, 순위는 표본이 적을수록 0 쪽으로 당긴 값(shrunk)으로 매긴다.
"""
from __future__ import annotations

import bisect
import math
from datetime import date, timedelta

from perf import BENCH, STALE_DAYS, Series
from strategy import ALIAS, live_track

MIN_W = 0.01            # 신규 편입으로 치는 최소 비중
SCALE_FROM, SCALE_TO, SCALE_X = 0.01, 0.02, 3.0   # 소량(1% 미만) → 2% 이상, 3배 이상으로 키운 경우
HORIZONS = {"m3": 91, "m6": 182, "m12": 365}
LOOKBACK_Q = 8          # 재진입 판정: 직전 분기에는 없었지만 그 전 8개 분기 안에 들고 있던 종목
SHRINK = 10             # 순위용 보정: 평균 초과수익 × n / (n + SHRINK)
TOP_GURUS = 5
COMBO_N = 8             # 실제 추적용 조합의 종목 수
TYPE_KO = {"new": "완전 신규", "reentry": "재진입", "scaleup": "소량 → 대폭 확대"}


def _plus(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def _tick(tickers: dict, c: str) -> str | None:
    t = tickers.get(c)
    return ALIAS.get(t, t) if t else None


def _wmap(q: dict, tickers: dict) -> dict[str, float]:
    """한 분기 13F 의 티커별 비중 (전체 평가액 대비)."""
    tot = q.get("total") or sum(x["v"] for x in q["h"].values()) or 1
    out: dict[str, float] = {}
    for c, x in q["h"].items():
        t = _tick(tickers, c) or c
        out[t] = out.get(t, 0) + x["v"] / tot
    return out


def events(qs: list[dict], tickers: dict) -> list[dict]:
    """한 대가의 신규 편입 사건: [{period, filed, t, w, type}] (오래된 순). 첫 분기는 비교할 직전 분기가 없어 뺀다."""
    ws = [_wmap(q, tickers) for q in qs]
    out = []
    for i in range(1, len(qs)):
        cur, prev = ws[i], ws[i - 1]
        names = {(_tick(tickers, c) or c): x["n"] for c, x in qs[i]["h"].items()}
        for t, w in cur.items():
            pw = prev.get(t, 0)
            if pw == 0 and w >= MIN_W:
                held_before = any(t in ws[j] for j in range(max(0, i - 1 - LOOKBACK_Q), i - 1))
                kind = "reentry" if held_before else "new"
            elif 0 < pw < SCALE_FROM and w >= SCALE_TO and w >= pw * SCALE_X:
                kind = "scaleup"
            else:
                continue
            out.append({"period": qs[i]["period"], "filed": qs[i]["filed"], "t": t, "n": names.get(t, ""), "w": round(w, 4), "type": kind})
    return out


def fwd_excess(e: dict, px: dict[str, Series], today: str) -> dict[str, float | None]:
    """공시일 종가에 사서 3·6·12개월 뒤까지의 수익률 − 같은 기간 S&P500. 아직 지나지 않은 기간·시세 없는 종목은 None."""
    s, b = px.get(e["t"]), px.get(BENCH)
    out: dict[str, float | None] = {k: None for k in HORIZONS}
    if not s or not b:
        return out
    p0, b0 = s.at(e["filed"]), b.at(e["filed"])
    if not p0 or not b0:
        return out
    for k, d in HORIZONS.items():
        end = _plus(e["filed"], d)
        if end > today or s.d[-1] < _plus(end, -10):  # 기간이 안 지났거나 그 전에 시세가 끊김(상장폐지·합병)
            continue
        out[k] = (s.at(end) / p0 - 1) - (b.at(end) / b0 - 1)
    return out


def stats(xs: list[float]) -> dict:
    """평균·중앙값·승률·95% 신뢰 구간·표본 보정값."""
    n = len(xs)
    if not n:
        return {"n": 0, "mean": None, "median": None, "win": None, "ci": None, "shrunk": None}
    mean = sum(xs) / n
    srt = sorted(xs)
    med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    half = 1.96 * sd / math.sqrt(n) if n > 1 else None
    r = lambda x: round(x, 4)  # noqa: E731
    return {"n": n, "mean": r(mean), "median": r(med), "win": round(sum(x > 0 for x in xs) / n, 3),
            "ci": [r(mean - half), r(mean + half)] if half is not None else None, "shrunk": r(mean * n / (n + SHRINK))}


def avg_price(s: Series, start: str, end: str) -> float | None:
    i, j = bisect.bisect_left(s.d, start), bisect.bisect_right(s.d, end)
    return sum(s.p[i:j]) / (j - i) if j > i else None


def scorecard(per: dict[str, list[dict]], tickers: dict, px: dict[str, Series], today: str, inv_names: dict[str, str]) -> tuple[list[dict], dict, dict[str, list[dict]]]:
    """(대가별 성적표, 유형별 전체 성적, 대가별 사건 목록)."""
    rows, by_type, ev_by = [], {k: [] for k in TYPE_KO}, {}
    for inv, qs in per.items():
        evs = [{**e, **fwd_excess(e, px, today)} for e in events(qs, tickers) if e["t"] in px]
        ev_by[inv] = evs
        row = {"id": inv, "name": inv_names.get(inv, inv), "events": len(evs)}
        for k in HORIZONS:
            row[k] = stats([e[k] for e in evs if e[k] is not None])
        row["types"] = {tp: {**stats([e["m6"] for e in evs if e["type"] == tp and e["m6"] is not None])} for tp in TYPE_KO}
        done = sorted((e for e in evs if e["m6"] is not None), key=lambda e: e["m6"])
        pick = lambda e: {"t": e["t"], "period": e["period"], "ex": round(e["m6"], 3)}  # noqa: E731
        row["best"], row["worst"] = [pick(e) for e in done[::-1][:2]], [pick(e) for e in done[:2]]
        rows.append(row)
        for e in evs:
            if e["m6"] is not None:
                by_type[e["type"]].append(e["m6"])
    rows.sort(key=lambda r: -(r["m6"]["shrunk"] if r["m6"]["shrunk"] is not None else -9))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["top"] = i < TOP_GURUS and (r["m6"]["shrunk"] or 0) > 0 and r["m6"]["n"] >= 5
    return rows, {tp: stats(xs) for tp, xs in by_type.items()}, ev_by


def current_picks(per, tickers, px, today: str, card: list[dict], ev_by: dict[str, list[dict]]) -> dict:
    """이번 분기(가장 최근 공시 분기) 신규 편입 종목과 점수.
    점수(0~100) = 그 대가의 신규 편입 성적 순위 50 + 편입 비중 30(8% 이상이면 만점) + 함께 새로 산 대가 수 20(3명 이상이면 만점)."""
    latest = max((qs[-1]["period"] for qs in per.values() if qs), default=None)
    if not latest:
        return {"period": None, "picks": []}
    rank = {r["id"]: r for r in card}
    n = max(len(card) - 1, 1)
    guru_pts = lambda inv: (0.0 if (rank[inv]["m6"]["shrunk"] or 0) <= 0 else 1 - (rank[inv]["rank"] - 1) / n)  # noqa: E731 — 성적이 시장 이하인 대가는 0점
    by_t: dict[str, dict] = {}
    for inv, qs in per.items():
        if not qs or qs[-1]["period"] != latest or (date.fromisoformat(today) - date.fromisoformat(latest)).days > STALE_DAYS:
            continue
        q = qs[-1]
        pd_ = date.fromisoformat(q["period"])
        qstart = date(pd_.year, pd_.month - 2, 1).isoformat()  # 분기 첫날 (분기 말이 3·6·9·12월)
        for e in ev_by.get(inv, []):
            if e["period"] != latest:
                continue
            o = by_t.setdefault(e["t"], {"t": e["t"], "n": e["n"], "buyers": [], "filed": e["filed"], "qstart": qstart})
            o["buyers"].append({"id": inv, "w": e["w"], "type": e["type"], "pts": round(guru_pts(inv), 3), "top": rank[inv]["top"]})
            o["filed"] = min(o["filed"], e["filed"])
    picks = []
    for o in by_t.values():
        s = px[o["t"]]
        best = max(o["buyers"], key=lambda b: (b["pts"], b["w"]))
        score = 100 * (0.5 * best["pts"] + 0.3 * min(max(b["w"] for b in o["buyers"]) / 0.08, 1) + 0.2 * min((len(o["buyers"]) - 1) / 2, 1))
        now, qa, pf = s.p[-1], avg_price(s, o["qstart"], latest), s.at(o["filed"])
        picks.append({"t": o["t"], "n": o["n"], "score": round(score), "buyers": sorted(o["buyers"], key=lambda b: -b["w"]),
                      "p": round(now, 2), "vs_q": round(now / qa - 1, 4) if qa else None, "vs_filed": round(now / pf - 1, 4) if pf else None,
                      "by_top": any(b["top"] for b in o["buyers"])})
    picks.sort(key=lambda p: (-p["score"], p["t"]))
    return {"period": latest, "picks": picks}


def build(per, tickers, px, today: str, inv_names: dict[str, str], ledger: dict | None = None) -> tuple[dict, dict]:
    card, types, ev_by = scorecard(per, tickers, px, today, inv_names)
    now = current_picks(per, tickers, px, today, card, ev_by)
    # 실제 추적용 조합: 성적 상위 대가가 새로 산 종목 중 점수 상위 COMBO_N 개를 같은 비중으로 (없으면 추적하지 않는다)
    combo = [p["t"] for p in now["picks"] if p["by_top"]][:COMBO_N]
    now["combo"] = combo
    ledger, live = live_track(ledger or {}, "newpick" if combo else None, now["period"], {t: 1 / len(combo) for t in combo}, px, today)
    out = {"as_of": today, "min_w": MIN_W, "horizons": HORIZONS, "type_ko": TYPE_KO, "gurus": card, "types": types, "now": now,
           "n_events": sum(len(v) for v in ev_by.values())}
    if live:
        out["live"] = live
    return out, ledger
