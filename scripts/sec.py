"""SEC EDGAR 공시 읽기: 13F(분기 보유종목), Form 4(대주주 장내매매), 13D·13G(5% 이상 지분).

모두 공개 데이터이며 키가 필요 없다. 투자자 기관 번호(CIK)의 제출 목록(submissions JSON)에서 출발한다.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta

from common import SEC, log

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
BROWSE = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}&dateb=&owner=include&count=40"
FORMS_13DG = ("SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A", "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A")


def filings(sub: dict) -> list[dict]:
    """submissions JSON 의 최근 제출 목록을 행 단위로."""
    r = sub.get("filings", {}).get("recent", {})
    keys = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument")
    return [dict(zip(keys, row)) for row in zip(*(r.get(k, []) for k in keys))]


def _strip_ns(xml: str) -> ET.Element:
    """네임스페이스(ns1: 등)를 지워 태그 이름만으로 찾을 수 있게."""
    xml = re.sub(r"^\s*<\?xml[^>]*\?>", "", xml)
    xml = re.sub(r'\sxmlns(:\w+)?="[^"]*"', "", xml)
    xml = re.sub(r'\s\w+:\w+="[^"]*"', "", xml)  # xsi:schemaLocation 같은 접두어 속성
    xml = re.sub(r"<(/?)\w+:", r"<\1", xml)
    return ET.fromstring(xml)


def _t(el: ET.Element | None, path: str, default: str = "") -> str:
    if el is None:
        return default
    x = el.find(path)
    return (x.text or "").strip() if x is not None and x.text else default


def _num(s: str) -> float:
    try:
        return float(s.replace(",", ""))
    except (AttributeError, ValueError):
        return 0.0


# ── 13F ──────────────────────────────────────────────────────

def parse_infotable(xml: str, filed: str = "") -> dict[str, dict]:
    """정보표(informationTable) → CUSIP 별 {issuer, value(USD), shares}. 옵션(PUT/CALL)·채권(PRN) 행은 뺀다.
    2023-01-03 이전 제출분은 금액이 천 달러 단위라 1000 을 곱한다. 그 뒤에도 천 달러 단위로 내는 곳이 있어
    (예: Duquesne, Baupost) 주당 가격 중앙값이 1달러 미만이면 천 달러 단위로 본다."""
    root = _strip_ns(xml)
    mult = 1000 if filed and filed < "2023-01-03" else 1
    out: dict[str, dict] = {}
    for row in root.iter("infoTable"):
        if _t(row, "putCall") or _t(row, "shrsOrPrnAmt/sshPrnamtType", "SH").upper() != "SH":
            continue
        cusip = _t(row, "cusip").upper()
        if not cusip:
            continue
        o = out.setdefault(cusip, {"issuer": _t(row, "nameOfIssuer").title(), "value": 0.0, "shares": 0.0})
        o["value"] += _num(_t(row, "value")) * mult
        o["shares"] += _num(_t(row, "shrsOrPrnAmt/sshPrnamt"))
    if mult == 1:
        prices = sorted(o["value"] / o["shares"] for o in out.values() if o["shares"] > 0 and o["value"] > 0)
        if prices and prices[len(prices) // 2] < 1:
            for o in out.values():
                o["value"] *= 1000
    return out


def find_infotable(sec: SEC, cik: int, acc: str) -> str | None:
    base = ARCHIVE.format(cik=cik, acc=acc.replace("-", ""))
    items = sec.json(f"{base}/index.json").get("directory", {}).get("item", [])
    xmls = [i for i in items if i["name"].lower().endswith(".xml") and i["name"].lower() != "primary_doc.xml"]
    if not xmls:
        return None
    best = next((i for i in xmls if "info" in i["name"].lower()), None) or max(xmls, key=lambda i: int(i.get("size") or 0))
    return f"{base}/{best['name']}"


def latest_13f(sec: SEC, cik: int, rows: list[dict]) -> list[dict]:
    """가장 최근 13F-HR 두 건(서로 다른 기준 분기). 정정(/A)은 쓰지 않는다."""
    picked, periods = [], set()
    for f in rows:
        if f["form"] != "13F-HR" or f["reportDate"] in periods:
            continue
        url = find_infotable(sec, cik, f["accessionNumber"])
        if not url:
            continue
        f = {**f, "holdings": parse_infotable(sec.text(url), f["filingDate"]),
             "url": f"{ARCHIVE.format(cik=cik, acc=f['accessionNumber'].replace('-', ''))}/{f['accessionNumber']}-index.htm"}
        picked.append(f)
        periods.add(f["reportDate"])
        if len(picked) == 2:
            break
    return picked


def compare(cur: dict[str, dict], prev: dict[str, dict], threshold: float) -> list[dict]:
    """두 분기 보유 비교 → 종목별 BUY/HOLD/SELL. flow = 수량 변화 × 분기말 주가(추정 순매수)."""
    total = sum(v["value"] for v in cur.values()) or 1
    out = []
    for cusip in set(cur) | set(prev):
        c, p = cur.get(cusip), prev.get(cusip)
        shares, pshares = (c or {}).get("shares", 0.0), (p or {}).get("shares", 0.0)
        value, pvalue = (c or {}).get("value", 0.0), (p or {}).get("value", 0.0)
        price = value / shares if shares else (pvalue / pshares if pshares else 0.0)
        if not pshares:
            sig, label, chg = "BUY", "신규 편입", None
        elif not shares:
            sig, label, chg = "SELL", "전량 매도", -100.0
        else:
            chg = round((shares / pshares - 1) * 100, 1)
            sig, label = ("BUY", "비중 확대") if chg >= threshold else ("SELL", "비중 축소") if chg <= -threshold else ("HOLD", "유지")
        out.append({"cusip": cusip, "issuer": (c or p)["issuer"], "ticker": None, "sector": "",
                    "value": round(value), "prev_value": round(pvalue), "shares": round(shares), "prev_shares": round(pshares),
                    "change_pct": chg, "flow": round((shares - pshares) * price), "signal": sig, "label": label,
                    "weight": round(value / total * 100, 2)})
    return out


# ── Form 4 ───────────────────────────────────────────────────

def parse_form4(xml: str, investor_cik: int) -> dict | None:
    """투자자가 '보고자'인 장내 매매만. 투자자 회사 자체가 발행사인 신고(다른 임원의 매매)는 건너뛴다."""
    root = _strip_ns(xml)
    issuer_cik = int(_t(root, "issuer/issuerCik", "0") or 0)
    owners = {int(_t(o, "reportingOwnerId/rptOwnerCik", "0") or 0) for o in root.iter("reportingOwner")}
    if issuer_cik == investor_cik or investor_cik not in owners:
        return None
    buy = sell = 0.0
    bval = sval = 0.0
    for tx in root.iter("nonDerivativeTransaction"):
        code = _t(tx, "transactionCoding/transactionCode")
        sh = _num(_t(tx, "transactionAmounts/transactionShares/value"))
        px = _num(_t(tx, "transactionAmounts/transactionPricePerShare/value"))
        if code == "P":
            buy += sh
            bval += sh * px
        elif code == "S":
            sell += sh
            sval += sh * px
    name = _t(root, "issuer/issuerName").title()
    ticker = _t(root, "issuer/issuerTradingSymbol").upper() or None
    if buy >= sell and buy:
        return {"ticker": ticker, "issuer": name, "signal": "BUY", "title": f"{name} 장내 매수",
                "detail": f"{buy:,.0f}주 매수" + (f" · 평균 ${bval / buy:,.2f}" if bval else "")}
    if sell:
        return {"ticker": ticker, "issuer": name, "signal": "SELL", "title": f"{name} 장내 매도",
                "detail": f"{sell:,.0f}주 매도" + (f" · 평균 ${sval / sell:,.2f}" if sval else "")}
    return {"ticker": ticker, "issuer": name, "signal": None, "title": f"{name} 지분 변동", "detail": "장내 매매가 아닌 변동(증여·전환 등)"}


def form4_xml_url(cik: int, f: dict) -> str:
    doc = f["primaryDocument"].split("/")[-1]  # xslF345X05/xxx.xml → xxx.xml (원본 XML)
    return f"{ARCHIVE.format(cik=cik, acc=f['accessionNumber'].replace('-', ''))}/{doc}"


# ── 13D · 13G ────────────────────────────────────────────────

def parse_index_parties(html: str) -> dict:
    """공시 목차 페이지에서 (Subject)·(Filed by) 회사 이름과 CIK."""
    out = {}
    for name, role, cik in re.findall(r'class="companyName">\s*([^<(]+?)\s*\((Subject|Filed by|Reporting)\)[\s\S]*?CIK[^\d]*(\d{4,10})', html):
        out.setdefault(role, {"name": name.strip().title(), "cik": int(cik)})
    return out


def event_from_13dg(form: str, subject: dict) -> dict:
    name = subject["name"]
    amend = form.endswith("/A")
    if amend:
        return {"issuer": name, "signal": None, "title": f"{name} 지분 변동 신고",
                "detail": "13D는 경영 참여 목적, 13G는 단순 투자 목적 신고 (정정)"}
    active = "13D" in form
    return {"issuer": name, "signal": "BUY", "title": f"{name} 5% 이상 지분 취득" + ("(경영 참여 가능)" if active else "(단순 투자)"),
            "detail": "13D는 경영 참여 목적, 13G는 단순 투자 목적 신고"}


def events(sec: SEC, cik: int, rows: list[dict], days: int, limit: int, ticker_of_cik: dict[int, str]) -> list[dict]:
    """최근 days 일 Form 4·13D/G 를 타임라인 항목으로 (최대 limit 건 내용 읽기)."""
    since = (date.today() - timedelta(days=days)).isoformat()
    out, read = [], 0
    for f in rows:
        if f["filingDate"] < since:
            break
        if read >= limit:
            break
        form = f["form"]
        try:
            if form == "4":
                read += 1
                ev = parse_form4(sec.text(form4_xml_url(cik, f)), cik)
                if not ev:
                    continue
                form_label = "Form 4"
            elif form in FORMS_13DG:
                read += 1
                base = ARCHIVE.format(cik=cik, acc=f["accessionNumber"].replace("-", ""))
                parties = parse_index_parties(sec.text(f"{base}/{f['accessionNumber']}-index.htm"))
                subj = parties.get("Subject")
                if not subj or subj["cik"] == cik:  # 투자자 회사 자체에 대한 남의 신고는 제외
                    continue
                ev = {**event_from_13dg(form, subj), "ticker": ticker_of_cik.get(subj["cik"])}
                form_label = form.replace("SCHEDULE ", "SC ")
            else:
                continue
        except Exception as e:  # noqa: BLE001 — 한 건 실패로 전체를 멈추지 않게
            log.warning("filing %s %s failed: %s", form, f["accessionNumber"], e)
            continue
        out.append({"date": f["filingDate"], "form": form_label, **ev,
                    "url": f"{ARCHIVE.format(cik=cik, acc=f['accessionNumber'].replace('-', ''))}/{f['accessionNumber']}-index.htm"})
    return out
