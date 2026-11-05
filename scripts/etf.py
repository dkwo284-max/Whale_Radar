"""ETF 등락 레이더의 순수 계산부 (네트워크·pandas 없이 테스트 가능).

- 이름으로 레버리지·인버스·VIX(변동성) ETF 를 걸러내고
- 미국 상장 ETF 를 미국 / 중국 / 인도 / 기타(해외) 로 나누고
- 종가 시계열로 1일·7일·14일·30일·90일·180일·YTD·1년 수익률(오늘 기준, 하루 전 기준)을 계산하고
- 화면용 행(row)·스파크라인·주간 시계열을 만든다.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

PERIODS = ["1d", "7d", "14d", "30d", "90d", "180d", "ytd", "1y"]
_DAYS = {"7d": 7, "14d": 14, "30d": 30, "90d": 90, "180d": 180, "1y": 365}

# ---------------------------------------------------------------- 제외 규칙 (이름 기준)

# 채권형 '단기(Short-Term)·초단기(Ultra-Short)' 는 인버스·레버리지가 아니다
_BONDISH = r"(income|bond|treasury|treasuries|duration|term|municipal|muni|maturity|government|gov't|credit|floating|t-bill|bill|cash|corporate|high yield|tips|inflation|mortgage|fixed)"
_LEV = re.compile(
    r"(?<![\w.])(1\.25|1\.5|1\.75|2|3|4|5)\s?x\b|\b-\s?[1-3]\s?x\b|\b(2x|3x)\b|\bultra\s?pro\b|\bultrapro\b|\bultrashort\b|"
    r"\bleveraged?\b|\blevered\b|\bdaily target\b|\bdirexion daily\b|\bproshares ultra\b|\bt-rex\b|\btradr\b|"
    r"\b(bull|bear)\s(2|3)x\b|\b(2|3)x\s(long|short)\b|\bdaily\s(2|3)x\b|\bmicrosectors\b.*\b(2|3)x\b",
    re.I,
)
_INV = re.compile(r"\binverse\b|\bbear\b|\bultrashort\b|\bdecliners?\b|\bshort\b|\bhedged? short\b|\b-1x\b", re.I)
_VIX = re.compile(r"\bvix\b|\bvolatility\b|\bvol futures\b|\bvixy\b", re.I)
_VOL_OK = re.compile(r"(low|min(imum)?|managed|reduced|lower|minimum)[\s-]+vol(atility)?|volatility[\s-]+(weighted|wtd|adjusted|controlled|managed)|\bmin vol\b", re.I)


def exclusion_reason(name: str) -> str | None:
    """레버리지 / 인버스 / VIX 면 이유를, 아니면 None."""
    n = " " + (name or "") + " "
    if _VIX.search(n) and not _VOL_OK.search(n):
        return "vix"
    if _LEV.search(n):
        # '1.5X' 같은 표기가 없는 채권 'Ultra-Short Income' 은 레버리지가 아님
        if re.search(r"\bultra[\s-]?short\b", n, re.I) and re.search(_BONDISH, n, re.I) and not re.search(r"\bultrashort\b", n, re.I):
            pass
        else:
            return "leveraged"
    m = _INV.search(n)
    if m:
        word = m.group(0).lower()
        if word == "short":
            after = n[m.end():m.end() + 30]
            before = n[max(0, m.start() - 12):m.start()]
            if re.match(r"[\s-]*" + _BONDISH, after, re.I) or re.search(r"ultra[\s-]*$", before, re.I) or re.search(r"(long|long/)\s*$", before, re.I):
                return None
            if re.search(r"\blong[/\s-]+short\b|\bshort[/\s-]+long\b|\bshort[\s-]+interest\b|\bshort[\s-]+(&|and)\s+long", n, re.I):
                return None  # 롱숏 전략은 인버스가 아님
        return "inverse"
    return None


# ---------------------------------------------------------------- 나라 분류 (미국 상장 ETF)

_FAMILIES = re.compile(r"^\s*(global x|globalx)\s+", re.I)
_EX_CHINA = re.compile(r"\bex[\s-]?china\b", re.I)
_EX_INDIA = re.compile(r"\bex[\s-]?india\b", re.I)
_CHINA = re.compile(r"\bchina\b|\bchinese\b|\bhong kong\b|\bcsi\b|\bshanghai\b|\bshenzhen\b|\brenminbi\b|\byuan\b|\bchinext\b|\bhang seng\b|\bstar\s?50\b", re.I)
_INDIA = re.compile(r"\bindia\b|\bindian\b|\bnifty\b|\bsensex\b", re.I)
_FOREIGN = re.compile(
    r"international|\bintl\b|\bglobal\b|\bworld\b|\bemerging\b|\bdeveloped\b|\bex[\s-]?u\.?s\.?\b|\beafe\b|\bacwi\b|all[\s-]country|"
    r"\bforeign\b|\bnon[\s-]?u\.?s\.?\b|\beurope\b|\beuro\b|\beurozone\b|\bjapan\b|\bnikkei\b|\bkorea\b|\btaiwan\b|\bbrazil\b|\bmexico\b|\bcanada\b|"
    r"\baustralia\b|\bgermany\b|\bgerman\b|\bfrance\b|\bitaly\b|\bspain\b|united kingdom|\bu\.?k\.?\b|\bswitzerland\b|\bswiss\b|\bsweden\b|"
    r"\bnetherlands\b|\bisrael\b|\bsaudi\b|\buae\b|\bqatar\b|\bafrica\b|\basia\b|\basian\b|\bpacific\b|\blatin\b|\bfrontier\b|"
    r"\bvietnam\b|\bindonesia\b|\bmalaysia\b|\bthailand\b|\bphilippines\b|\bsingapore\b|\bchile\b|\bperu\b|\bcolombia\b|"
    r"\bargentina\b|\bpoland\b|\bturkey\b|\bgreece\b|\bnordic\b|\bireland\b|\bbelgium\b|\baustria\b|\bnorway\b|"
    r"\bdenmark\b|\bfinland\b|\bnew zealand\b|\bkuwait\b|\begypt\b|\bnigeria\b|\bbric\b|\bmiddle east\b|\bgulf\b",
    re.I,
)


def us_listed_country(name: str) -> str:
    """미국 상장 ETF 가 어느 나라를 담는지: 'cn' | 'in' | 'us' | 'other'."""
    n = _FAMILIES.sub("", name or "")
    if _CHINA.search(n) and not _EX_CHINA.search(n):
        return "cn"
    if _INDIA.search(n) and not _EX_INDIA.search(n):
        return "in"
    if _FOREIGN.search(n):
        return "other"
    return "us"


# ---------------------------------------------------------------- 테마 (영문 이름 기준, 순서가 우선순위)

THEMES = [
    ("크립토", r"bitcoin|\bether(eum)?\b|crypto|blockchain|solana|\bxrp\b|digital asset"),
    ("반도체", r"semiconductor|\bsemi(s|conductors?)?\b|\bchips?\b"),
    ("AI·로봇", r"artificial intelligence|\bai\b|robot|automation|machine learning"),
    ("인터넷·소프트웨어", r"internet|software|cloud|cyber|e-?commerce|social media|\bsaas\b|digital|video gam|esports|metaverse"),
    ("기술", r"tech|nasdaq|innovation|information|fintech|5g|communication|\bdata\b|quantum|space"),
    ("금·귀금속", r"\bgold\b|silver|platinum|palladium|precious"),
    ("원자재", r"commodit|agricult|copper|metal|natural resources|lithium|uranium|rare earth|steel|timber|\boil\b|natural gas|crude|mining|miners"),
    ("클린에너지·EV", r"clean|solar|wind|renewable|battery|hydrogen|\bev\b|electric vehicle|new energy|carbon|sustainab|esg|climate|photovoltaic"),
    ("에너지", r"energy|\bmlp\b|pipeline|nuclear|coal|power"),
    ("채권·현금", r"treasury|treasuries|\bbond|income|duration|municipal|\bmuni|credit|aggregate|\btips\b|t-bill|\bbill\b|floating|mortgage|corporate|high yield|fixed|money market|cash|\bloan"),
    ("부동산", r"\breit|real estate|property|homebuild|housing"),
    ("헬스케어", r"health|biotech|pharma|medical|genomic|life science|drug"),
    ("금융", r"financ|\bbank|insur|capital market|broker|securities"),
    ("소비재", r"consumer|retail|staples|discretionary|luxury|leisure|travel|restaurant|liquor|food|beverage|consumption|gaming|media"),
    ("산업재·방산", r"industrial|aerospace|defen[cs]e|infrastructure|transport|airline|machinery|construction|military|shipping|manufactur"),
    ("유틸리티", r"utilit"),
    ("소재", r"material|chemical"),
    ("배당·인컴", r"dividend|yield|covered call|premium income|buywrite|option income|\bincome\b"),
    ("시장 대표지수", r"s&p|total (stock )?market|russell|\bdow\b|\b500\b|large[\s-]?cap|mid[\s-]?cap|small[\s-]?cap|equal weight|growth|value|momentum|quality|core|broad|composite|msci|ftse|csi 300|nifty 50|hang seng index|tracker|a50|sse 50|index"),
]
_THEME_RX = [(k, re.compile(rx, re.I)) for k, rx in THEMES]


def theme_of(name: str) -> str:
    n = _FAMILIES.sub("", name or "")
    for k, rx in _THEME_RX:
        if rx.search(n):
            return k
    return "기타"


# ---------------------------------------------------------------- 수익률

def _base_index(dates: list[str], end: int, target: str) -> int | None:
    """end 이하 위치 중 날짜가 target 이하인 마지막 위치. 없으면 None."""
    lo, hi, ans = 0, end, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if dates[mid] <= target:
            ans, lo = mid, mid + 1
        else:
            hi = mid - 1
    return ans


def period_returns(dates: list[str], closes: list[float], end: int | None = None) -> dict[str, float | None]:
    """dates(오름차순 ISO)·closes 로 end 위치 기준 기간 수익률(%) — 기준가가 없으면 None."""
    if end is None:
        end = len(dates) - 1
    out: dict[str, float | None] = {p: None for p in PERIODS}
    if end < 1 or end >= len(dates):
        return out
    last = closes[end]
    if not last or last <= 0:
        return out

    def pct(i):
        b = closes[i]
        return None if i is None or not b or b <= 0 else round((last / b - 1) * 100, 2)

    out["1d"] = pct(end - 1)
    d_end = date.fromisoformat(dates[end])
    for p, n in _DAYS.items():
        target = (d_end - timedelta(days=n)).isoformat()
        if dates[0] > target:  # 이력이 기간보다 짧음
            continue
        i = _base_index(dates, end, target)
        if i is not None and i < end:
            out[p] = pct(i)
    ytd_target = date(d_end.year - 1, 12, 31).isoformat()
    if dates[0] <= ytd_target:
        i = _base_index(dates, end, ytd_target)
        if i is not None and i < end:
            out["ytd"] = pct(i)
    return out


def avg_turnover(closes: list[float], volumes: list[float], n: int = 20) -> float:
    """최근 n 거래일 평균 거래대금(현지 통화)."""
    pairs = [(c, v) for c, v in zip(closes[-n:], volumes[-n:]) if c and v]
    return sum(c * v for c, v in pairs) / len(pairs) if pairs else 0.0


def spark(closes: list[float], n: int = 30) -> list[int]:
    """최근 n 개 종가를 첫 값=1000 인 정수로."""
    xs = [c for c in closes[-n:] if c]
    if len(xs) < 2:
        return []
    b = xs[0]
    return [round(c / b * 1000) for c in xs]


def weekly(dates: list[str], closes: list[float], weeks: int = 53) -> dict:
    """마지막 날부터 7일 간격으로 거슬러 올라간 주간 종가(첫 값=1000). {'end': 마지막 날, 'v': [...] (오래된 → 최신)}"""
    if not dates:
        return {"end": None, "v": []}
    end = len(dates) - 1
    d_end = date.fromisoformat(dates[end])
    vals = []
    for k in range(weeks):
        target = (d_end - timedelta(days=7 * k)).isoformat()
        if dates[0] > target:
            break
        i = _base_index(dates, end, target)
        vals.append(closes[i])
    vals.reverse()
    if not vals or not vals[0]:
        return {"end": dates[end], "v": []}
    b = vals[0]
    return {"end": dates[end], "v": [round(v / b * 1000) for v in vals]}


# ---------------------------------------------------------------- 분할 보정

_SPLIT_K = [2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 50, 100]


def fix_splits(closes: list[float], volumes: list[float], tol: float = 0.04) -> tuple[list[float], list[float]]:
    """Yahoo 가 분할(병합)을 아직 반영하지 않은 날을 찾아 그 이전 가격을 맞춘다.
    레버리지를 뺀 ETF 가 하루에 35% 넘게, 그것도 정수배(1/2, 1/3, 2, 3 …)에 가깝게 움직이면 분할로 본다."""
    cs, vs = list(closes), list(volumes)
    for i in range(1, len(cs)):
        if not cs[i - 1] or not cs[i]:
            continue
        ratio = cs[i] / cs[i - 1]
        if 0.65 < ratio < 1.35:
            continue
        for k in _SPLIT_K:
            for target in (1 / k, float(k)):
                if abs(ratio / target - 1) <= tol:
                    for j in range(i):
                        cs[j] *= target
                        vs[j] /= target
                    break
            else:
                continue
            break
    return cs, vs


def has_glitch(closes: list[float], limit: float = 0.45) -> bool:
    """분할 보정 뒤에도 하루 45% 넘게 움직인 날이 있으면 시세 오류로 본다(레버리지를 뺀 ETF 기준)."""
    return any(a and b and abs(b / a - 1) > limit for a, b in zip(closes, closes[1:]))


# ---------------------------------------------------------------- 행 만들기

def make_row(meta: dict, dates: list[str], closes: list[float], volumes: list[float]) -> dict | None:
    """meta: {t, y, n, c, l, x, cur, th?}. 이력이 20일 미만이면 None."""
    pts = [(d, c, v) for d, c, v in zip(dates, closes, volumes) if c is not None and c == c and c > 0]
    if len(pts) < 20:
        return None
    ds = [p[0] for p in pts]
    cs, vs = fix_splits([float(p[1]) for p in pts], [float(p[2] or 0) for p in pts])
    if has_glitch(cs):
        return "glitch"
    now = period_returns(ds, cs)
    prev = period_returns(ds, cs, len(ds) - 2)
    row = {
        "t": meta["t"], "y": meta.get("y", meta["t"]), "n": meta["n"], "c": meta["c"], "l": meta["l"],
        "x": meta.get("x", ""), "cur": meta.get("cur", "USD"),
        "th": meta.get("th") or theme_of(meta.get("en") or meta["n"]),
        "p": round(cs[-1], 4), "d": ds[-1],
        "r": [now[p] for p in PERIODS], "rp": [prev[p] for p in PERIODS],
        "tv": round(avg_turnover(cs, vs)),
        "sp": spark(cs),
    }
    return row, weekly(ds, cs)


def drop_stale(rows: list[dict], max_lag_days: int = 7) -> tuple[list[dict], int]:
    """시장(c,l)별 최신 날짜보다 max_lag_days 넘게 멈춘 ETF 를 뺀다."""
    latest: dict[tuple, str] = {}
    for r in rows:
        k = (r["c"], r["l"])
        latest[k] = max(latest.get(k, ""), r["d"])
    keep, dropped = [], 0
    for r in rows:
        lim = (date.fromisoformat(latest[(r["c"], r["l"])]) - timedelta(days=max_lag_days)).isoformat()
        if r["d"] < lim:
            dropped += 1
        else:
            keep.append(r)
    return keep, dropped


def rank_union(rows: list[dict], n: int = 25) -> set[str]:
    """나라 × 상장(전체/미국/현지) × 기간 × 상승/하락 조합마다 상위 n 개 — 상세정보를 받을 후보."""
    out: set[str] = set()
    for c in ("us", "cn", "in"):
        for lst in ("all", "us", "local"):
            sub = [r for r in rows if r["c"] == c and (lst == "all" or r["l"] == lst)]
            for i in range(len(PERIODS)):
                have = [r for r in sub if r["r"][i] is not None]
                have.sort(key=lambda r: r["r"][i])
                out.update(r["y"] for r in have[:n])
                out.update(r["y"] for r in have[-n:])
    return out


def market_as_of(rows: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in rows:
        k = f"{r['c']}-{r['l']}"
        out[k] = max(out.get(k, ""), r["d"])
    return out
