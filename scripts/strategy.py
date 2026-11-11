"""대가 조합(하이브리드) 전략 시뮬레이션 → web/data/strategy.json

여러 대가의 13F 를 섞어 만든 포트폴리오를 분기마다 다시 짜서, 과거에 그대로 따라 했다면 어땠는지 계산한다. AI 를 쓰지 않는다.

- 네 가지 조합 + 혼합
  · momentum  성적 상위 대가 추종: 직전 4개 분기 복제 수익률 상위 4명의 상위 10종목
  · consensus 컨센서스 종목: 대가 3명 이상이 함께 든 종목, 겹치는 대가 수로 가중
  · conviction 확신 종목: 각 대가의 비중 1·2위 종목을 같은 비중으로
  · newbuy    신규 편입 추종: 직전 분기에 없다가 새로 산(비중 1% 이상) 종목
  · blend     위 네 가지를 25% 씩
  · all       (비교용) 모든 대가를 같은 비중으로
- 두 기준
  · follow 공시일 기준(기본): 분기 말 + 47일(13F 마감 직후)에, 그때까지 '공시된' 13F 만 보고 다시 짠다 — 실제로 따라 할 수 있는 값
  · qe     분기 말 기준(참고): 분기 말 보유를 그날 바로 알았다고 가정 — 실제로는 불가능한 상한선
- 미래 정보를 쓰지 않는다: 재조정일에 이미 공시된 13F 와 그날까지의 수익률만 사용한다.
- 빠진 것: 거래 비용·세금·환율, 13F 에 없는 공매도·현금·해외 주식. 대가 명단 자체가 '지금 유명한 사람'이라 생기는 생존 편향도 있다.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from perf import BENCH, STALE_DAYS, Series, chain, metrics, sample, turnover, window
from perf import weights as _weights

LAG_DAYS = 47       # 분기 말 → 재조정일 (마감 45일 + 주말 여유)
LOOKBACK = 4        # 성적 상위 대가를 고를 때 보는 분기 수 (모든 조합이 이만큼 지난 뒤 함께 시작)
MAX_NAMES = 20      # 한 조합의 최대 종목 수
COST = 0.002        # 사고팔 때마다 거래 금액의 0.2% (수수료·호가 차이·환전 비용의 대략값)
SIZES = (10, 20, 30)  # 혼합 조합을 몇 종목으로 줄여 따라 할지 비교
FX = {"KRW": "KRW=X", "NZD": "NZD=X"}  # 1달러 = ? (투자금 계산기용)
MIN_SHARE = 0.6     # 시뮬레이션은 대가의 60% 이상이 13F 를 낸 분기부터 시작
MDD_GUARD = 0.15    # 추천 후보: 최대 낙폭이 S&P500 보다 이만큼 넘게 나쁘면 제외
ALIAS = {"GOOG": "GOOGL", "BRK.A": "BRK.B", "BRK-A": "BRK.B", "FOX": "FOXA", "NWS": "NWSA"}  # 같은 회사의 다른 주식 종류는 하나로
DEFAULTS = {"momentum": {"top": 4, "lookback": LOOKBACK, "per": 10}, "consensus": {"min_holders": 3}, "conviction": {"top": 2},
            "newbuy": {"min_w": 0.01}}
VARIANTS = {"momentum": [{"top": 3}, {"top": 5}, {"lookback": 2}, {"top": 3, "lookback": 2}, {"top": 5, "lookback": 2}],
            "consensus": [{"min_holders": 2}, {"min_holders": 4}], "conviction": [{"top": 1}, {"top": 3}],
            "newbuy": [{"min_w": 0.005}, {"min_w": 0.02}]}
LABELS = {"momentum": "성적 상위 대가 추종", "consensus": "컨센서스 종목", "conviction": "확신 종목", "newbuy": "신규 편입 추종",
          "blend": "네 가지 혼합", "all": "전체 대가 균등"}
ORDER = ["momentum", "consensus", "conviction", "newbuy", "blend", "all"]


def plus_days(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def schedule(per: dict[str, list[dict]], today: str, basis: str) -> list[dict]:
    """재조정 일정: [{period, date}] (오래된 순). follow 는 분기 말 + LAG_DAYS, qe 는 분기 말. 아직 오지 않은 날은 뺀다."""
    cnt: dict[str, int] = {}
    for qs in per.values():
        for q in qs:
            cnt[q["period"]] = cnt.get(q["period"], 0) + 1
    need = max(2, math.ceil(len(per) * MIN_SHARE))  # 대가 대부분의 13F 가 있는 분기부터 (한두 명뿐인 옛 분기는 뺀다)
    periods = sorted(cnt)
    first = next((p for p in periods if cnt[p] >= need), None)
    periods = [p for p in periods if first and p >= first]
    out = [{"period": p, "date": plus_days(p, LAG_DAYS) if basis == "follow" else p} for p in periods]
    return [s for s in out if s["date"] <= today]


def snapshot(per: dict[str, list[dict]], when: str, basis: str, fresh_from: str) -> dict[str, tuple[dict, dict | None]]:
    """when 시점에 알 수 있던 대가별 최신 13F 와 그 직전 13F. fresh_from(직전 분기 말)보다 오래된 13F 뿐인 대가는 뺀다(공시 중단)."""
    key = "filed" if basis == "follow" else "period"
    out = {}
    for inv, qs in per.items():
        known = [i for i, q in enumerate(qs) if q[key] <= when]
        if not known:
            continue
        i = known[-1]
        if qs[i]["period"] < fresh_from:
            continue
        out[inv] = (qs[i], qs[i - 1] if i > 0 else None)
    return out


def weights(h: dict, tickers: dict[str, str | None]) -> dict[str, float]:
    """perf.weights 와 같지만 같은 회사의 다른 주식 종류(GOOG/GOOGL 등)를 합친다."""
    out: dict[str, float] = {}
    for t, v in _weights(h, tickers).items():
        t = ALIAS.get(t, t)
        out[t] = out.get(t, 0) + v
    return out


def norm(w: dict[str, float], cap: int = MAX_NAMES) -> dict[str, float]:
    top = dict(sorted(w.items(), key=lambda kv: (-kv[1], kv[0]))[:cap])
    s = sum(top.values())
    return {t: v / s for t, v in top.items()} if s > 0 else {}


def mix(parts: list[dict[str, float]], cap: int = MAX_NAMES) -> dict[str, float]:
    """여러 포트폴리오를 같은 비중으로 합친다 (빈 것은 뺀다)."""
    parts = [p for p in parts if p]
    out: dict[str, float] = {}
    for p in parts:
        for t, v in p.items():
            out[t] = out.get(t, 0) + v / len(parts)
    return norm(out, cap)


# ── 조합 규칙 (순수 함수) ─────────────────────────────────

def pick_momentum(ws: dict[str, dict[str, float]], trail: dict[str, float | None], top: int, per: int) -> tuple[dict[str, float], list[str]]:
    """직전 수익률 상위 top 명의 상위 per 종목을 대가별 같은 비중으로."""
    ranked = sorted((i for i in ws if trail.get(i) is not None), key=lambda i: (-trail[i], i))[:top]
    return mix([norm(ws[i], per) for i in ranked]), ranked


def pick_consensus(ws: dict[str, dict[str, float]], min_holders: int) -> dict[str, float]:
    """min_holders 명 이상이 든 종목. 비중 = 겹치는 대가 수 (동률이면 평균 비중이 큰 종목 먼저)."""
    cnt: dict[str, int] = {}
    tot: dict[str, float] = {}
    for w in ws.values():
        for t, v in w.items():
            cnt[t] = cnt.get(t, 0) + 1
            tot[t] = tot.get(t, 0) + v
    keep = sorted((t for t in cnt if cnt[t] >= min_holders), key=lambda t: (-cnt[t], -tot[t], t))[:MAX_NAMES]
    return norm({t: float(cnt[t]) for t in keep})


def pick_conviction(ws: dict[str, dict[str, float]], top: int) -> dict[str, float]:
    """각 대가의 비중 상위 top 종목을 한 표씩 (여러 대가가 고르면 그만큼 커진다)."""
    out: dict[str, float] = {}
    for w in ws.values():
        for t, _ in sorted(w.items(), key=lambda kv: (-kv[1], kv[0]))[:top]:
            out[t] = out.get(t, 0) + 1.0
    return norm(out)


def new_buys(q: dict, prev: dict | None, tickers: dict[str, str | None], min_w: float) -> dict[str, float]:
    """직전 13F 에 없던 종목 중 비중 min_w 이상: {티커: 13F 비중}. 직전 13F 가 없으면(첫 분기) 비어 있다."""
    if not prev:
        return {}
    tot = q.get("total") or sum(x["v"] for x in q["h"].values()) or 1
    old = {ALIAS.get(tickers.get(c), tickers.get(c)) or c for c in prev["h"]} | set(prev["h"])
    out: dict[str, float] = {}
    for c, x in q["h"].items():
        t = tickers.get(c)
        t = ALIAS.get(t, t) if t else t
        if t and c not in old and t not in old and x["v"] / tot >= min_w:
            out[t] = out.get(t, 0) + x["v"] / tot
    return out


def pick_newbuy(snap: dict[str, tuple[dict, dict | None]], tickers: dict[str, str | None], min_w: float) -> dict[str, float]:
    """대가들이 이번 분기에 새로 산 종목. 비중 = 대가 포트폴리오 안 비중의 합 (확신이 큰 신규 매수일수록 크게, 한 종목 최대 15%)."""
    out: dict[str, float] = {}
    for q, prev in snap.values():
        for t, v in new_buys(q, prev, tickers, min_w).items():
            out[t] = out.get(t, 0) + min(v, 0.15)
    return norm(out)


# ── 시뮬레이션 ────────────────────────────────────────────

def clone_returns(per, tickers, px, sched, basis) -> list[dict[str, float | None]]:
    """구간 k(재조정일 k → k+1)에 대가별 복제 포트폴리오 수익률 — 성적 상위 대가를 고를 때 쓴다 (끝난 구간만)."""
    out = []
    for k in range(len(sched) - 1):
        snap = snapshot(per, sched[k]["date"], basis, sched[max(k - 1, 0)]["period"])
        out.append({i: window(weights(q["h"], tickers), px, sched[k]["date"], sched[k + 1]["date"])["ret"] for i, (q, _) in snap.items()})
    return out


def trailing(rets: list[dict[str, float | None]], k: int, lookback: int) -> dict[str, float | None]:
    """재조정 k 직전 lookback 개 구간의 누적 수익률 (하나라도 없으면 None)."""
    if k < lookback:
        return {}
    out: dict[str, float | None] = {}
    for i in {i for r in rets[k - lookback:k] for i in r}:
        v = 1.0
        for r in rets[k - lookback:k]:
            if r.get(i) is None:
                v = None
                break
            v *= 1 + r[i]
        out[i] = v - 1 if v is not None else None
    return out


def portfolios(per, tickers, px, sched, basis, params: dict | None = None) -> list[dict]:
    """재조정마다 조합별 포트폴리오: [{period, date, w: {조합: {티커: 비중}}, gurus: [...], n: 대가 수}] (LOOKBACK 번째부터)."""
    p = {k: {**v, **((params or {}).get(k) or {})} for k, v in DEFAULTS.items()}
    rets = clone_returns(per, tickers, px, sched, basis)
    out, last_new = [], {}
    for k in range(LOOKBACK, len(sched)):
        snap = snapshot(per, sched[k]["date"], basis, sched[k - 1]["period"])
        ws = {i: _priced(weights(q["h"], tickers), px) for i, (q, _) in snap.items()}
        ws = {i: w for i, w in ws.items() if w}
        mom, gurus = pick_momentum(ws, trailing(rets, k, p["momentum"]["lookback"]), p["momentum"]["top"], p["momentum"]["per"])
        new = norm(_priced(pick_newbuy(snap, tickers, p["newbuy"]["min_w"]), px)) or last_new  # 새로 산 종목이 없는 분기는 직전 조합을 그대로 든다
        last_new = new
        w = {"momentum": mom, "consensus": pick_consensus(ws, p["consensus"]["min_holders"]),
             "conviction": pick_conviction(ws, p["conviction"]["top"]), "newbuy": new, "all": mix(list(ws.values()), 40)}
        four = [w[s] for s in ("momentum", "consensus", "conviction", "newbuy")]
        w["blend"] = mix(four, 30)
        for n in SIZES[:-1]:  # 같은 혼합을 상위 n 종목만 남겨 다시 100% 로
            w[f"blend{n}"] = mix(four, n)
        out.append({"period": sched[k]["period"], "date": sched[k]["date"], "w": w, "gurus": gurus, "n": len(ws)})
    return out


def _priced(w: dict[str, float], px: dict[str, Series]) -> dict[str, float]:
    """시세가 없는 종목(상장폐지·우선주 등)은 살 수 없으니 뺀다 (비중은 나중에 다시 100% 로)."""
    return {t: v for t, v in w.items() if t in px}


def run(ports: list[dict], px: dict[str, Series], today: str, name: str) -> dict:
    """한 조합의 구간별 수익률·누적 곡선·지표 (S&P500 과 같은 구간으로 비교)."""
    wins, bwins, rows = [], [], []
    net, prev_w = 1.0, {}
    for k, pf in enumerate(ports):
        end = ports[k + 1]["date"] if k + 1 < len(ports) else today
        w = pf["w"][name] or {BENCH: 1.0}  # 고를 종목이 없으면 지수를 든다
        a, b = window(w, px, pf["date"], end), window({BENCH: 1.0}, px, pf["date"], end)
        wins.append(a)
        bwins.append(b)
        traded = sum(abs(w.get(t, 0) - prev_w.get(t, 0)) for t in set(w) | set(prev_w))  # 사고판 금액 / 자산 (처음엔 1 = 전부 매수)
        net *= (1 - COST * traded) * (1 + (a["ret"] or 0))
        prev_w = w
        rows.append({"period": pf["period"], "date": pf["date"], "ret": a["ret"], "spy": b["ret"], "n": len(pf["w"][name]),
                     "partial": k + 1 == len(ports)})
    d, v = chain(wins)
    bd, bv = chain(bwins)
    m, ms = metrics(d, v, today), metrics(bd, bv, today)
    done = [r for r in rows if not r["partial"] and r["ret"] is not None and r["spy"] is not None]
    years = (date.fromisoformat(d[-1]) - date.fromisoformat(ports[0]["date"])).days / 365.25 if d else 0
    tot, btot = (v[-1] - 1 if v else None), (bv[-1] - 1 if bv else None)
    cagr = lambda r: (1 + r) ** (1 / years) - 1 if r is not None and r > -1 and years > 0 else None  # noqa: E731
    half = len(done) // 2
    ex = lambda rs: (_cum(r["ret"] for r in rs) - _cum(r["spy"] for r in rs)) if rs else None  # noqa: E731
    net_tot = net - 1 if rows else None
    m.update({"total_net": net_tot, "cagr_net": cagr(net_tot),
              "excess_net": (cagr(net_tot) - cagr(btot)) if cagr(net_tot) is not None and cagr(btot) is not None else None})
    m.update({"total": tot, "cagr": cagr(tot), "spy_total": btot, "spy_cagr": cagr(btot), "spy_mdd": ms["mdd"],
              "excess": (cagr(tot) - cagr(btot)) if cagr(tot) is not None and cagr(btot) is not None else None,
              "win": sum(1 for r in done if r["ret"] > r["spy"]) / len(done) if done else None, "quarters": len(done), "years": round(years, 2),
              "ex_h1": ex(done[:half]), "ex_h2": ex(done[half:]), "turnover": turnover([pf["w"][name] for pf in ports]),
              "names": round(sum(r["n"] for r in rows) / len(rows), 1) if rows else None})
    sd, sv = sample(d, v)
    bmap = dict(zip(bd, bv))
    return {"metrics": m, "rows": [{**r, "ret": _r(r["ret"]), "spy": _r(r["spy"])} for r in rows],
            "curve": {"d": sd, "v": sv, "spy": [round(bmap[x], 4) if x in bmap else None for x in sd]}}


def _cum(rs) -> float:
    v = 1.0
    for r in rs:
        v *= 1 + r
    return v - 1


def _r(x: float | None, n: int = 4) -> float | None:
    return round(x, n) if x is not None else None


def sensitivity(per, tickers, px, sched, basis, today) -> dict[str, dict]:
    """규칙의 숫자(몇 명·몇 종목)를 조금 바꿨을 때 연 수익률이 얼마나 달라지는지 — 범위가 넓으면 우연히 맞춘 숫자일 수 있다."""
    out = {}
    for name, vs in VARIANTS.items():
        cs = []
        for v in vs:
            c = run(portfolios(per, tickers, px, sched, basis, {name: v}), px, today, name)["metrics"]["cagr"]
            if c is not None:
                cs.append(c)
        if cs:
            out[name] = {"min": _r(min(cs)), "max": _r(max(cs)), "n": len(cs)}
    return out


def changes(ports: list[dict], name: str, min_move: float = 0.015) -> dict:
    """직전 재조정과 견준 변화: 새로 들어온 종목·빠진 종목·비중이 크게(1.5%p 이상) 바뀐 종목."""
    if len(ports) < 2:
        return {"prev": None, "in": [], "out": [], "up": [], "down": []}
    new, old = ports[-1]["w"][name], ports[-2]["w"][name]
    row = lambda t, d: {"t": t, "w": round(new.get(t, 0), 4), "d": round(d, 4)}  # noqa: E731
    both = [(t, new[t] - old[t]) for t in new if t in old]
    return {"prev": ports[-2]["period"],
            "in": [row(t, new[t]) for t in sorted(new, key=lambda t: -new[t]) if t not in old],
            "out": [{"t": t, "w": 0, "d": round(-old[t], 4)} for t in sorted(old, key=lambda t: -old[t]) if t not in new],
            "up": [row(t, d) for t, d in sorted(both, key=lambda x: -x[1]) if d >= min_move],
            "down": [row(t, d) for t, d in sorted(both, key=lambda x: x[1]) if d <= -min_move]}


def holdings_now(pf: dict, name: str, snap: dict[str, tuple[dict, dict | None]], tickers, inv_names: dict[str, str],
                 px: dict[str, Series] | None = None) -> list[dict]:
    """지금 조합의 종목·비중과 그 종목을 든 대가 (p = 최근 종가, 달러 — 투자금 계산기용)."""
    issuer: dict[str, str] = {}
    holders: dict[str, list[str]] = {}
    for i, (q, _) in snap.items():
        for t in weights(q["h"], tickers):
            holders.setdefault(t, []).append(i)
        for c, x in q["h"].items():
            if tickers.get(c):
                issuer.setdefault(ALIAS.get(tickers[c], tickers[c]), x["n"])
    last = lambda t: round(px[t].p[-1], 2) if px and t in px and px[t].p else None  # noqa: E731
    return [{"t": t, "w": round(w, 4), "n": issuer.get(t, ""), "by": holders.get(t, []), "p": last(t)}
            for t, w in sorted(pf["w"][name].items(), key=lambda kv: (-kv[1], kv[0]))]


def recommend(strats: dict[str, dict], sens: dict[str, dict] | None = None) -> dict:
    """규칙 기반 추천: ① 앞 절반·뒤 절반 '모두'에서 S&P500 을 이기고 ② 최대 낙폭이 지수보다 MDD_GUARD 넘게 나쁘지 않고
    ③ 규칙의 숫자를 조금 바꿔도 연 수익률이 지수보다 높은(우연히 맞춘 숫자가 아닌) 조합 중, 더 나빴던 절반의 초과 수익이 가장 큰 것.
    (전체 수익률 1등을 고르면 한 번의 대박에 끌려가기 쉽다.) 그런 조합이 없으면 그 사실을 알리고 가장 덜 나빴던 것을 보여 준다."""
    cand = {k: s["metrics"] for k, s in strats.items() if k != "all" and s["metrics"].get("ex_h1") is not None and s["metrics"].get("ex_h2") is not None}
    if not cand:
        return {"pick": None, "robust": False, "reasons": [], "warnings": ["비교할 만큼 기록이 쌓이지 않았어요."]}
    floor = {k: min(m["ex_h1"], m["ex_h2"]) for k, m in cand.items()}
    calm = lambda m: m.get("mdd") is None or m.get("spy_mdd") is None or m["mdd"] >= m["spy_mdd"] - MDD_GUARD  # noqa: E731
    sens = sens or {}
    sturdy = lambda k: k not in sens or cand[k].get("spy_cagr") is None or sens[k]["min"] > cand[k]["spy_cagr"]  # noqa: E731
    ok = [k for k in cand if floor[k] > 0 and calm(cand[k]) and sturdy(k)]
    pick = max(ok or cand, key=lambda k: (floor[k], cand[k].get("excess") or -9))
    m = cand[pick]
    robust = bool(ok)
    pct = lambda x: f"{x * 100:+.1f}%"  # noqa: E731
    reasons = [f"{m['quarters']}개 분기 중 앞 절반 {pct(m['ex_h1'])}p, 뒤 절반 {pct(m['ex_h2'])}p (S&P500 대비 누적 초과 수익)",
               f"연 {pct(m['cagr'])} · S&P500 연 {pct(m['spy_cagr'])} · 분기 승률 {m['win'] * 100:.0f}%" if m.get("cagr") is not None and m.get("win") is not None else "",
               f"가장 크게 빠졌을 때 {m['mdd'] * 100:.0f}% (S&P500 {m['spy_mdd'] * 100:.0f}%)" if m.get("mdd") is not None and m.get("spy_mdd") is not None else ""]
    warnings = []
    if not robust:
        warnings.append("세 조건(앞·뒤 절반 모두 시장을 이김, 낙폭이 지나치지 않음, 규칙을 조금 바꿔도 시장을 이김)을 모두 채운 조합이 없어요. 지수(S&P500)를 중심에 두고 일부만 참고하는 편이 안전합니다.")
    if m.get("mdd") is not None and m.get("spy_mdd") is not None and m["mdd"] < m["spy_mdd"] - 0.05:
        warnings.append("수익이 높은 대신 하락 폭이 S&P500 보다 컸어요.")
    if m.get("turnover") is not None and m["turnover"] > 0.5:
        warnings.append(f"분기마다 종목의 약 {m['turnover'] * 100:.0f}% 를 바꿔야 해요 (거래 비용·세금은 계산에 없음).")
    best = max(cand, key=lambda k: cand[k].get("total") or -9)
    out_why = {k: ("뒤 절반이나 앞 절반에서 시장에 졌어요" if floor[k] <= 0 else "낙폭이 S&P500 보다 훨씬 컸어요" if not calm(cand[k])
                   else "규칙의 숫자를 조금 바꾸면 시장에 져요") for k in cand if k not in ok}
    return {"pick": pick, "robust": robust, "best_return": best, "passed": ok, "failed": out_why, "reasons": [r for r in reasons if r], "warnings": warnings}


def guru_scores(per, tickers, px, sched, today, inv_names: dict[str, str]) -> list[dict]:
    """따라 하기 좋은 대가: 공시일 기준 최근 12개 분기의 초과 수익·승률, 공시 지연으로 잃는 수익, 회전율로 순위를 매긴다."""
    rets = clone_returns(per, tickers, px, sched, "follow")
    qe_s = schedule(per, today, "qe")
    qe = clone_returns(per, tickers, px, qe_s, "qe")
    spy = [window({BENCH: 1.0}, px, sched[k]["date"], sched[k + 1]["date"])["ret"] for k in range(len(sched) - 1)]
    last = sched[-1] if sched else None
    rows = []
    for inv, qs in per.items():
        if not qs or not last or (date.fromisoformat(today) - date.fromisoformat(qs[-1]["period"])).days > STALE_DAYS:
            continue
        pairs = [(r[inv], s) for r, s in zip(rets, spy) if r.get(inv) is not None and s is not None][-12:]
        if len(pairs) < 6:
            continue
        ex = _cum(a for a, _ in pairs) - _cum(b for _, b in pairs)
        win = sum(1 for a, b in pairs if a > b) / len(pairs)
        y1 = _cum(a for a, _ in pairs[-4:]) - _cum(b for _, b in pairs[-4:])
        q12 = [r[inv] for r in qe if r.get(inv) is not None][-len(pairs):]
        delay = (_cum(a for a, _ in pairs) - _cum(q12)) if len(q12) == len(pairs) else None  # 음수 = 공시를 기다리는 동안 놓친 수익
        to = turnover([weights(q["h"], tickers) for q in qs[-9:]])
        rows.append({"id": inv, "name": inv_names.get(inv, inv), "n": len(pairs), "ex3y": _r(ex), "win": _r(win, 2), "ex1y": _r(y1),
                     "delay": _r(delay), "turnover": _r(to, 2)})
    def ranks(key, rev):  # 큰 값이 좋으면 rev=True
        order = sorted(rows, key=lambda r: (r[key] if r[key] is not None else (-9 if rev else 9)), reverse=rev)
        return {r["id"]: i for i, r in enumerate(order)}
    rk = [ranks("ex3y", True), ranks("win", True), ranks("ex1y", True), ranks("delay", True), ranks("turnover", False)]
    wt = [3, 2, 2, 1, 1]
    n = max(len(rows) - 1, 1)
    for r in rows:
        r["score"] = round(100 * (1 - sum(w * k[r["id"]] for w, k in zip(wt, rk)) / (sum(wt) * n)))
        why = []
        if r["ex3y"] is not None:
            why.append(f"최근 {r['n']}개 분기 S&P500 대비 {r['ex3y'] * 100:+.0f}%p")
        why.append(f"분기 승률 {r['win'] * 100:.0f}%")
        if r["delay"] is not None and r["delay"] < -0.1:
            why.append("공시를 기다리는 동안 수익을 많이 놓침")
        if r["turnover"] is not None:
            why.append("종목을 자주 바꿈" if r["turnover"] > 0.35 else "오래 들고 가서 따라 하기 쉬움" if r["turnover"] < 0.12 else "")
        r["why"] = [w for w in why if w]
    return sorted(rows, key=lambda r: -r["score"])


def build(per: dict[str, list[dict]], tickers: dict[str, str | None], px: dict[str, Series], today: str, inv_names: dict[str, str]) -> dict:
    out = {"generated_at": date.today().isoformat(), "as_of": today, "benchmark": BENCH, "lag_days": LAG_DAYS, "labels": LABELS,
           "params": DEFAULTS, "bases": {}}
    for basis in ("follow", "qe"):
        sched = schedule(per, today, basis)
        ports = portfolios(per, tickers, px, sched, basis)
        if not ports:
            continue
        strats = {name: run(ports, px, today, name) for name in ORDER}
        block = {"start": ports[0]["date"], "rebalances": len(ports), "strategies": strats}
        if basis == "follow":
            last = ports[-1]
            snap = snapshot(per, last["date"], basis, sched[-2]["period"] if len(sched) > 1 else last["period"])
            block["sensitivity"] = sensitivity(per, tickers, px, sched, basis, today)
            block["now"] = {"period": last["period"], "date": last["date"], "next": plus_days(_next_quarter_end(last["period"]), LAG_DAYS),
                            "investors": last["n"], "gurus": last["gurus"],
                            "portfolios": {name: holdings_now(last, name, snap, tickers, inv_names, px)
                                           for name in [n for n in ORDER if n != "all"] + [f"blend{n}" for n in SIZES[:-1]]},
                            "changes": {name: changes(ports, name) for name in [n for n in ORDER if n != "all"] + [f"blend{n}" for n in SIZES[:-1]]}}
            keep = ("cagr", "cagr_net", "total", "mdd", "win", "turnover", "excess", "ex_h1", "ex_h2", "vol", "r1y", "spy_cagr", "spy_mdd")
            block["sizes"] = {str(n): {k: (run(ports, px, today, "blend" if n == SIZES[-1] else f"blend{n}")["metrics"]).get(k) for k in keep} for n in SIZES}
            out["fx"] = {c: round(px[s].p[-1], 4) for c, s in FX.items() if s in px and px[s].p}
            out["cost"] = COST
            block["history"] = [{"period": p["period"], "date": p["date"], "gurus": p["gurus"]} for p in ports]
            out["recommend"] = recommend(strats, block["sensitivity"])
            out["gurus"] = guru_scores(per, tickers, px, sched, today, inv_names)
        out["bases"][basis] = block
    return out


def _next_quarter_end(p: str) -> str:
    d = date.fromisoformat(p) + timedelta(days=1)   # 다음 분기 첫날
    m = d.month + 3
    y, m = (d.year + 1, m - 12) if m > 12 else (d.year, m)
    return (date(y, m, 1) - timedelta(days=1)).isoformat()
