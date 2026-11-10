"""종목 정보: 13F 의 CUSIP → 티커(OpenFIGI, 키 불필요), 티커 → 섹터(SEC 업종코드 SIC). 결과는 cache/ 에 쌓아 다시 묻지 않는다."""
from __future__ import annotations

import time

import httpx

from common import CACHE, SEC, log, read_json, write_json

FIGI = "https://api.openfigi.com/v3/mapping"
TICKERS = "https://www.sec.gov/files/company_tickers.json"

# SEC 업종코드 분류와 GICS 가 다른 대표 종목은 직접 지정
SECTOR_OVERRIDE = {
    "GOOGL": "커뮤니케이션", "GOOG": "커뮤니케이션", "META": "커뮤니케이션", "NFLX": "커뮤니케이션", "DIS": "커뮤니케이션",
    "AMZN": "경기소비재", "TSLA": "경기소비재", "COST": "필수소비재", "WMT": "필수소비재", "V": "금융", "MA": "금융",
    "PYPL": "금융", "UBER": "산업재", "BRK.B": "금융", "BRK.A": "금융",
}


def sic_sector(sic: int | None, name: str = "") -> str:
    """SEC SIC 업종코드 → 한국어 섹터 (GICS 에 가깝게 근사)."""
    if not sic:
        return "ETF·기타"
    s = int(sic)
    if 1300 <= s <= 1399 or 2900 <= s <= 2999 or s in (4610, 4612, 4613, 4922, 4923):
        return "에너지"
    if 2830 <= s <= 2836 or 3840 <= s <= 3851 or 8000 <= s <= 8099 or s in (5122, 6324, 8731):
        return "헬스케어"
    if 4800 <= s <= 4899 or 2700 <= s <= 2799 or 7810 <= s <= 7841 or s in (7812, 7822):
        return "커뮤니케이션"
    if 3570 <= s <= 3579 or 3600 <= s <= 3699 or 3670 <= s <= 3679 or 7370 <= s <= 7379 or s in (3825, 3826, 3827, 3829):
        return "IT"
    if 4900 <= s <= 4949 or 4960 <= s <= 4991:
        return "유틸리티"
    if s in (6798,) or 6500 <= s <= 6553:
        return "부동산"
    if 6000 <= s <= 6799:
        return "ETF·기타" if s in (6221, 6726) else "금융"
    if 2000 <= s <= 2199 or 2840 <= s <= 2844 or 5400 <= s <= 5499 or s in (5141, 5149, 5331, 5399, 5912):
        return "필수소비재"
    if 1000 <= s <= 1499 or 2600 <= s <= 2699 or 2800 <= s <= 2829 or 2850 <= s <= 2899 or 3200 <= s <= 3399:
        return "소재"
    if 5000 <= s <= 5999 or 7000 <= s <= 7299 or 7900 <= s <= 7999 or 2200 <= s <= 2399 or 3711 <= s <= 3716 or s in (3021, 3140, 3630, 3651, 3942, 3944, 3949, 8200):
        return "경기소비재"
    if 1500 <= s <= 1799 or 3400 <= s <= 3569 or 3580 <= s <= 3599 or 3700 <= s <= 3799 or 4000 <= s <= 4799 or 4950 <= s <= 4959 or 7380 <= s <= 7389 or 8700 <= s <= 8748 or s == 3812:
        return "산업재"
    return "ETF·기타"


def figi_lookup(cusips: list[str], client: httpx.Client, pause: float = 2.6) -> dict[str, str | None]:
    """OpenFIGI: CUSIP 10개씩, 키 없이 분당 25회까지. 미국 상장 보통주 티커를 고른다."""
    out: dict[str, str | None] = {}
    for i in range(0, len(cusips), 10):
        chunk = cusips[i:i + 10]
        try:
            r = client.post(FIGI, json=[{"idType": "ID_CUSIP", "idValue": c} for c in chunk])
            if r.status_code == 429:
                time.sleep(30)
                r = client.post(FIGI, json=[{"idType": "ID_CUSIP", "idValue": c} for c in chunk])
            r.raise_for_status()
            for c, res in zip(chunk, r.json()):
                rows = res.get("data") or []
                us = [x for x in rows if x.get("exchCode") == "US"] or rows
                t = (us[0].get("ticker") if us else None)
                out[c] = t.replace("/", ".") if t else None
        except Exception as e:  # noqa: BLE001
            log.warning("OpenFIGI failed for %d cusips: %s", len(chunk), e)
        time.sleep(pause)
    return out


class Meta:
    def __init__(self, sec: SEC, client: httpx.Client | None = None):
        self.sec = sec
        self.client = client or httpx.Client(timeout=40, headers={"Content-Type": "application/json"})
        self.cusip: dict[str, str | None] = read_json(CACHE / "cusip_ticker.json", {})
        self.sector: dict[str, str] = read_json(CACHE / "ticker_sector.json", {})
        self._tickers: dict[str, int] | None = None
        self._cik_ticker: dict[int, str] | None = None

    def _load_tickers(self) -> None:
        if self._tickers is None:
            try:
                rows = self.sec.json(TICKERS).values()
            except Exception as e:  # noqa: BLE001
                log.warning("company_tickers failed: %s", e)
                rows = []
            self._tickers = {r["ticker"].upper().replace("-", "."): int(r["cik_str"]) for r in rows}
            self._cik_ticker = {}
            for t, c in self._tickers.items():
                self._cik_ticker.setdefault(c, t)

    def cik_to_ticker(self) -> dict[int, str]:
        self._load_tickers()
        return self._cik_ticker or {}

    # OpenFIGI 가 못 찾는 해외 등록(CINS: G·H·N·Y·M 으로 시작) 미국 상장 종목 — 직접 확인한 것만
    MANUAL = {"H1467J104": "CB", "G0896C103": "TBBB", "G7997R103": "STX", "N62509109": "NAMS", "G11448100": "BTDR", "G25508105": "CRH",
              "N07059210": "ASML", "N6596X109": "NXPI", "G25457105": "CRDO", "Y2573F102": "FLEX", "G0403H108": "AON", "G66721104": "NCLH",
              "G4412G101": "HLF", "G61188127": "LBTYK", "G0750C108": "AXTA", "H17182108": "CRSP", "G16910120": "BLSH", "G5509L101": "LIVN",
              "G48833118": "WFRD", "Y2065G121": "DHT", "N00985106": "AER", "G3934V109": "GENI", "G0593M107": "AZN", "G6359F137": "NBR",
              "M6158M104": "ITRN", "N53745100": "LYB", "M6191J100": "FROG", "M2197Q107": "CLBT", "N3167Y103": "RACE", "G51502105": "JCI",
              "G7997W102": "SDRL"}

    def tickers_for(self, cusips: set[str]) -> dict[str, str | None]:
        todo = sorted(c for c in cusips if c not in self.cusip)
        if todo:
            log.info("OpenFIGI: %d new cusip(s)", len(todo))
            self.cusip.update(figi_lookup(todo, self.client))
            write_json(CACHE / "cusip_ticker.json", self.cusip)
        return {c: self.cusip.get(c) or self.MANUAL.get(c) for c in cusips}

    def sector_for(self, ticker: str | None, name: str = "") -> str:
        if not ticker:
            return "ETF·기타"
        if ticker in SECTOR_OVERRIDE:
            return SECTOR_OVERRIDE[ticker]
        if ticker not in self.sector:
            self._load_tickers()
            cik = (self._tickers or {}).get(ticker)
            sic = None
            if cik:
                try:
                    sic = self.sec.json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").get("sic")
                except Exception as e:  # noqa: BLE001
                    log.warning("sic %s failed: %s", ticker, e)
            self.sector[ticker] = sic_sector(int(sic) if sic else None, name)
        return self.sector[ticker]

    def save(self) -> None:
        write_json(CACHE / "ticker_sector.json", self.sector)
