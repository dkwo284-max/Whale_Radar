"""마켓 레이더 · 글로벌(패권 이동) 계산 로직 — 네트워크 없이 테스트할 수 있는 순수 함수들.

- 나라·지역 ETF 의 기간 수익률, 미국(SPY) 대비 상대수익, 뉴질랜드달러 환산
- 패권 레이더 지역 점수(▲−▼)와 시장 반응(상대수익)을 잇는 분면
- 13F 큰손 매수·매도를 지역으로 나눈 집계
- 규칙 기반 '관찰 후보' 점수와 근거·반대 근거·리스크 문장
투자 권유가 아니라 검토할 후보를 좁히는 규칙입니다.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from etf import _DAYS, _base_index, period_returns

MP = ["7d", "30d", "90d", "180d", "ytd", "1y"]   # 글로벌 화면에서 쓰는 기간
MP_KO = {"7d": "7일", "30d": "30일", "90d": "90일", "180d": "180일", "ytd": "연초 대비", "1y": "1년"}
I90 = MP.index("90d")
I30 = MP.index("30d")
I1Y = MP.index("1y")
CATS = ["성장", "증시", "통화·금융", "안보·군사", "기술·무역"]


# ---------------------------------------------------------------- 수익률

def rets(dates: list[str], closes: list[float], end: int | None = None) -> list[float | None]:
    pr = period_returns(dates, closes, end)
    return [pr[p] for p in MP]


def to_nzd(dates: list[str], closes: list[float], fdates: list[str], fcloses: list[float]) -> tuple[list[str], list[float]]:
    """달러 가격 → 뉴질랜드달러 가격. fcloses 는 '1 NZD = ? USD' (NZDUSD=X). 환율은 그 날 또는 직전 값."""
    od, oc = [], []
    j = -1
    for d, c in zip(dates, closes):
        while j + 1 < len(fdates) and fdates[j + 1] <= d:
            j += 1
        if j < 0 or not fcloses[j]:
            continue
        od.append(d)
        oc.append(c / fcloses[j])
    return od, oc


def rel(a: list[float | None], b: list[float | None]) -> list[float | None]:
    """수익률 차(%p). 어느 한쪽이 비면 None."""
    return [None if x is None or y is None else round(x - y, 2) for x, y in zip(a, b)]


def rs_weekly(dates: list[str], closes: list[float], bdates: list[str], bcloses: list[float], weeks: int = 53) -> list[float]:
    """기준(미국) 대비 상대강도선. 7일 간격, 첫 값 = 100 (오래된 → 최신)."""
    if not dates or not bdates:
        return []
    end, bend = len(dates) - 1, len(bdates) - 1
    d_end = date.fromisoformat(dates[end])
    vals: list[float] = []
    for k in range(weeks):
        t = (d_end - timedelta(days=7 * k)).isoformat()
        if dates[0] > t or bdates[0] > t:
            break
        i, j = _base_index(dates, end, t), _base_index(bdates, bend, t)
        if i is None or j is None or not closes[i] or not bcloses[j]:
            break
        vals.append(closes[i] / bcloses[j])
    vals.reverse()
    if not vals:
        return []
    b = vals[0]
    return [round(v / b * 100, 1) for v in vals]


def rel_at(dates: list[str], closes: list[float], bdates: list[str], bcloses: list[float], days_back: int, period: str = "90d") -> float | None:
    """days_back 일 전 시점에서 본 period 상대수익(%p). 한 달 전 위치와 비교할 때 쓴다."""
    if not dates or not bdates:
        return None
    d_end = date.fromisoformat(dates[-1]) - timedelta(days=days_back)
    t = d_end.isoformat()
    i, j = _base_index(dates, len(dates) - 1, t), _base_index(bdates, len(bdates) - 1, t)
    if i is None or j is None:
        return None
    a, b = period_returns(dates, closes, i)[period], period_returns(bdates, bcloses, j)[period]
    return None if a is None or b is None else round(a - b, 2)


def changes(dates: list[str], vals: list[float]) -> list[float | None]:
    """수익률이 아니라 값의 차이(금리처럼 %p 로 보는 지표)."""
    if len(dates) < 2:
        return [None] * len(MP)
    end = len(dates) - 1
    d_end = date.fromisoformat(dates[end])
    out: list[float | None] = []
    for p in MP:
        if p == "ytd":
            t = date(d_end.year - 1, 12, 31).isoformat()
        else:
            t = (d_end - timedelta(days=_DAYS[p])).isoformat()
        if dates[0] > t:
            out.append(None)
            continue
        i = _base_index(dates, end, t)
        out.append(None if i is None or i >= end else round(vals[end] - vals[i], 2))
    return out


def vol_dd(closes: list[float], n: int = 252) -> tuple[float | None, float | None]:
    """최근 n 거래일 연환산 변동성(%)과 고점 대비 낙폭(%)."""
    c = [x for x in closes[-(n + 1):] if x and x > 0]
    if len(c) < 30:
        return None, None
    lr = [math.log(b / a) for a, b in zip(c, c[1:])]
    m = sum(lr) / len(lr)
    sd = math.sqrt(sum((x - m) ** 2 for x in lr) / max(1, len(lr) - 1))
    return round(sd * math.sqrt(252) * 100, 1), round((c[-1] / max(c) - 1) * 100, 1)


def flow_ratio(closes: list[float], vols: list[float], short: int = 20, long: int = 120) -> float | None:
    """최근 20거래일 평균 거래대금 ÷ 최근 120거래일 평균. 1보다 크면 거래가 늘고 있다."""
    def avg(n):
        pairs = [(c, v) for c, v in zip(closes[-n:], vols[-n:]) if c and v]
        return sum(c * v for c, v in pairs) / len(pairs) if pairs else 0.0
    if len(closes) < 60:
        return None
    a, b = avg(short), avg(long)
    return round(a / b, 2) if b > 0 else None


# ---------------------------------------------------------------- 패권 신호

def hegemony_regions(heg: dict) -> dict[str, dict]:
    """hegemony.json → {지역: {score, ups, downs, unknown, cells}}. 점수는 칸의 신호로 다시 계산(▲ +1, ▼ −1, 나머지 0)."""
    out: dict[str, dict] = {}
    for rid, r in (heg.get("regions") or {}).items():
        cells = r.get("cells") or []
        ups = [c["cat"] for c in cells if c.get("signal") == "▲"]
        downs = [c["cat"] for c in cells if c.get("signal") == "▼"]
        unknown = [c["cat"] for c in cells if c.get("signal") not in ("▲", "◆", "▼")]
        out[rid] = {"n": r.get("n", rid), "score": len(ups) - len(downs), "ups": ups, "downs": downs, "unknown": unknown, "cells": cells}
    return out


def score_history(heg: dict) -> dict[str, list[dict]]:
    """{지역: [{date, score}, …]} 오래된 → 최신."""
    out: dict[str, list[dict]] = {}
    for row in sorted(heg.get("log") or [], key=lambda x: x.get("date", "")):
        out.setdefault(row["region"], []).append({"date": row["date"], "score": row["score"]})
    return out


def quadrant(score: int | None, rel90: float | None) -> str | None:
    """신호(가로) × 시장 반응(세로) 네 칸."""
    if score is None or rel90 is None:
        return None
    s_up, m_up = score > 0, rel90 > 0
    if s_up and m_up:
        return "trend"      # 신호 강화 + 주가 강세: 추세 진행
    if s_up and not m_up:
        return "early"      # 신호 강화 + 주가 약세: 아직 가격에 반영 안 됨
    if not s_up and m_up:
        return "heat"       # 신호 약화/중립 + 주가 강세: 과열 주의
    return "avoid"          # 신호 약화/중립 + 주가 약세: 회피


QUAD_KO = {"trend": "추세 진행", "early": "아직 반영 안 됨 · 관찰 1순위", "heat": "과열 주의", "avoid": "회피"}


# ---------------------------------------------------------------- 13F 큰손

def smart_money(dash: dict, cfg: dict) -> dict:
    """큰손(13F)의 최근 분기 매수·매도를 지역으로 집계.
    반환: {지역: {buy: 매수한 투자자 수, sell: 매도한 투자자 수, buys: [티커…], sells: [티커…]}, '_t': {티커: {buy, sell}}}"""
    tick_region: dict[str, str] = {}
    for rg, ts in (cfg.get("adr") or {}).items():
        for t in ts:
            tick_region[t.upper()] = rg
    issuers = [(s.lower(), rg) for rg, ss in (cfg.get("adr_issuer") or {}).items() for s in ss]
    out: dict = {rg: {"buy": set(), "sell": set(), "buys": set(), "sells": set()} for rg in set(tick_region.values()) | {rg for _, rg in issuers}}
    per: dict[str, dict] = {}
    for iv in dash.get("investors") or []:
        for p in iv.get("positions") or []:
            t = (p.get("ticker") or "").upper()
            rg = tick_region.get(t)
            if not rg:
                nm = (p.get("issuer") or "").lower()
                rg = next((r for s, r in issuers if s in nm), None)
            if not rg:
                continue
            sig = p.get("signal")
            label = t or ((p.get("issuer") or "?").split() or ["?"])[0].upper()
            if sig == "BUY":
                out[rg]["buy"].add(iv["id"])
                out[rg]["buys"].add(label)
                per.setdefault(label, {"buy": set(), "sell": set()})["buy"].add(iv["id"])
            elif sig == "SELL":
                out[rg]["sell"].add(iv["id"])
                out[rg]["sells"].add(label)
                per.setdefault(label, {"buy": set(), "sell": set()})["sell"].add(iv["id"])
    res = {rg: {"buy": len(v["buy"]), "sell": len(v["sell"]), "buys": sorted(v["buys"]), "sells": sorted(v["sells"])} for rg, v in out.items()}
    res["_t"] = {t: {"buy": len(v["buy"]), "sell": len(v["sell"])} for t, v in per.items()}
    return res


# ---------------------------------------------------------------- 관찰 후보

def _signal_check(item: dict, regs: dict, cfg: dict) -> tuple[bool | None, str]:
    """지역 신호 칸: 통과 여부(True/False/None=평가 없음)와 설명."""
    ov = (cfg.get("sig_override") or {}).get(item["y"])
    if ov:
        r = regs.get(ov["rg"])
        cell = next((c for c in (r or {}).get("cells", []) if c.get("cat") == ov["cat"]), None)
        if not cell or cell.get("signal") not in ("▲", "◆", "▼"):
            return None, f"{r['n'] if r else ov['rg']} {ov['cat']} 신호 미확인"
        ok = cell["signal"] == ov["want"]
        return ok, f"{r['n']} {ov['cat']} {cell['signal']} ({cell.get('value', '')})"
    r = regs.get(item.get("rg"))
    if not r or len(r["unknown"]) >= len(CATS):
        return None, "패권 레이더가 평가하지 않는 지역(신호 없음)"
    nice = "·".join(r["ups"]) if r["ups"] else "없음"
    txt = f"{r['n']} 패권 신호 점수 {r['score']:+d} (▲ {nice})"
    return r["score"] > 0, txt


def score_candidate(item: dict, regs: dict, sm: dict, cfg: dict, th: dict | None = None) -> dict:
    """한 종목의 4가지 조건(신호·상대수익·큰손/거래대금·유동성)과 근거 문장."""
    th = th or cfg.get("thresholds") or {}
    min_to = float(th.get("min_turnover_usd", 5_000_000))
    min_fr = float(th.get("flow_ratio", 1.15))
    rg = item.get("rg")
    r90 = (item.get("rl") or [None] * len(MP))[I90]
    r30 = (item.get("rl") or [None] * len(MP))[I30]

    sig, sig_txt = _signal_check(item, regs, cfg)
    rel_ok = None if r90 is None else r90 > 0
    rel_txt = "상대수익 계산 불가" if r90 is None else f"미국(S&P 500) 대비 90일 {r90:+.1f}%p" + ("" if r30 is None else f" · 30일 {r30:+.1f}%p")

    own = (sm.get("_t") or {}).get(item["y"].upper())
    reg_sm = sm.get(rg) if rg else None
    buyers = sellers = 0
    who = ""
    if own and (own["buy"] or own["sell"]):
        buyers, sellers, who = own["buy"], own["sell"], item["y"]
    elif reg_sm and item.get("k") in ("region", "theme"):
        # 나라별 ETF 는 지역 전체 큰손 집계를 근거로 쓰지 않는다(스페인 ETF 에 독일·네덜란드 종목 매수를 붙이면 과장).
        buyers, sellers = reg_sm["buy"], reg_sm["sell"]
        who = (item.get("rn_ko") or rg) + " 관련 종목"
    sm_ok = buyers > 0 and buyers > sellers
    fr = item.get("fr")
    fr_ok = fr is not None and fr >= min_fr
    flow_ok = sm_ok or fr_ok
    parts = []
    if buyers or sellers:
        parts.append(f"큰손 13F {who}: 매수 {buyers}명 · 매도 {sellers}명")
    if fr is not None:
        parts.append(f"거래대금 20일 평균이 120일 평균의 {fr:.2f}배")
    flow_txt = " / ".join(parts) if parts else "큰손·거래대금 자료 없음"

    to = item.get("to") or 0
    liq_ok = to >= min_to
    liq_txt = f"20일 평균 거래대금 약 ${to / 1e6:.1f}M" + ("" if liq_ok else f" (기준 ${min_to / 1e6:.0f}M 미만)")

    checks = {"sig": sig, "rel": rel_ok, "flow": flow_ok if (buyers or sellers or fr is not None) else None, "liq": liq_ok}
    passed = sum(1 for v in checks.values() if v)
    missing = [k for k, v in checks.items() if v is not True]
    if passed == 4:
        status = "cand"
    elif passed == 3 and len(missing) == 1:
        status = "watch"
    else:
        status = "no"

    pros = []
    if sig:
        pros.append(sig_txt)
    if rel_ok:
        pros.append(rel_txt)
    if checks["flow"]:
        pros.append(flow_txt)
    if liq_ok and len(pros) < 3:
        pros.append(liq_txt)
    if item.get("dd") is not None and item["dd"] > -8 and len(pros) < 3:
        pros.append(f"고점 대비 {item['dd']:+.1f}%로 추세 유지")

    why = {"sig": sig_txt, "rel": rel_txt, "flow": flow_txt, "liq": liq_txt}
    name = {"sig": "지역 신호", "rel": "상대수익", "flow": "큰손·거래대금", "liq": "유동성"}
    if missing:
        k = missing[0]
        con = f"{name[k]} 조건 미충족: {why[k]}"
    elif item.get("dd") is not None and item["dd"] <= -15:
        con = f"고점 대비 {item['dd']:+.1f}%로 낙폭이 큼"
    elif item.get("vol") is not None and item["vol"] >= 30:
        con = f"연 변동성 {item['vol']:.0f}%로 흔들림이 큼"
    else:
        con = "네 가지 조건은 통과했지만 이미 많이 오른 뒤일 수 있어 진입 가격 확인이 필요함"

    risks = []
    cur = item.get("cur")
    fx1 = item.get("fx1y")
    if cur and fx1 is not None:
        n1 = (item.get("rn") or [None] * len(MP))[I1Y]
        u1 = (item.get("r") or [None] * len(MP))[I1Y]
        tail = "" if n1 is None or u1 is None else f" (이 ETF 1년 수익: 달러 {u1:+.1f}% · 뉴질랜드달러 {n1:+.1f}%)"
        risks.append(f"환율: {cur} 1년 {fx1:+.1f}% (달러 대비){tail}")
    risks.append("정책: " + (cfg.get("risk") or {}).get(rg or "gl", "나라별 정책·통화 차이를 점검"))
    hold = item.get("hold") or []
    if hold:
        top3 = sum(h[2] for h in hold[:3])
        risks.append(f"집중도: 상위 3종목 {top3:.0f}% ({', '.join(h[0] for h in hold[:3])})")
    return {
        "y": item["y"], "n": item["n"], "rg": rg, "k": item.get("k"), "cur": cur, "status": status, "pass": passed,
        "checks": checks, "pros": pros[:3], "con": con, "risk": risks,
        "detail": {"sig": sig_txt, "rel": rel_txt, "flow": flow_txt, "liq": liq_txt},
    }


def rank_candidates(cands: list[dict]) -> list[dict]:
    """후보 → 관찰 대기 순, 같은 등급에서는 90일 상대수익이 큰 순."""
    order = {"cand": 0, "watch": 1, "no": 2}
    return sorted(cands, key=lambda c: (order[c["status"]], -(c.get("r90") if c.get("r90") is not None else -999)))
