from sec import compare, event_from_13dg, parse_form4, parse_index_parties, parse_infotable

INFO = """<?xml version="1.0" encoding="UTF-8"?>
<ns1:informationTable xmlns:ns1="http://www.sec.gov/edgar/document/thirteenf/informationtable" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <ns1:infoTable><ns1:nameOfIssuer>APPLE INC</ns1:nameOfIssuer><ns1:titleOfClass>COM</ns1:titleOfClass><ns1:cusip>037833100</ns1:cusip>
  <ns1:value>1000000</ns1:value><ns1:shrsOrPrnAmt><ns1:sshPrnamt>5000</ns1:sshPrnamt><ns1:sshPrnamtType>SH</ns1:sshPrnamtType></ns1:shrsOrPrnAmt></ns1:infoTable>
 <ns1:infoTable><ns1:nameOfIssuer>APPLE INC</ns1:nameOfIssuer><ns1:cusip>037833100</ns1:cusip>
  <ns1:value>200000</ns1:value><ns1:shrsOrPrnAmt><ns1:sshPrnamt>1000</ns1:sshPrnamt><ns1:sshPrnamtType>SH</ns1:sshPrnamtType></ns1:shrsOrPrnAmt></ns1:infoTable>
 <ns1:infoTable><ns1:nameOfIssuer>APPLE INC</ns1:nameOfIssuer><ns1:cusip>037833100</ns1:cusip><ns1:putCall>Put</ns1:putCall>
  <ns1:value>999</ns1:value><ns1:shrsOrPrnAmt><ns1:sshPrnamt>9</ns1:sshPrnamt><ns1:sshPrnamtType>SH</ns1:sshPrnamtType></ns1:shrsOrPrnAmt></ns1:infoTable>
</ns1:informationTable>"""


def test_parse_infotable_aggregates_and_skips_options():
    h = parse_infotable(INFO, "2026-08-14")
    assert h == {"037833100": {"issuer": "Apple Inc", "value": 1200000.0, "shares": 6000.0}}
    assert parse_infotable(INFO, "2022-11-14")["037833100"]["value"] == 1200000000.0   # 천 달러 단위 시절
    thousands = INFO.replace("<ns1:value>1000000</ns1:value>", "<ns1:value>1000</ns1:value>").replace("<ns1:value>200000</ns1:value>", "<ns1:value>200</ns1:value>")
    assert parse_infotable(thousands, "2026-08-14")["037833100"]["value"] == 1200000.0   # 2023 이후에도 천 달러 단위로 낸 경우


def test_compare_signals():
    cur = {"A": {"issuer": "A", "value": 110, "shares": 11}, "B": {"issuer": "B", "value": 100, "shares": 10},
           "N": {"issuer": "N", "value": 50, "shares": 5}}
    prev = {"A": {"issuer": "A", "value": 100, "shares": 10}, "B": {"issuer": "B", "value": 102, "shares": 10.2},
            "S": {"issuer": "S", "value": 80, "shares": 8}}
    got = {p["cusip"]: p for p in compare(cur, prev, 5)}
    assert (got["A"]["signal"], got["A"]["label"], got["A"]["change_pct"], got["A"]["flow"]) == ("BUY", "비중 확대", 10.0, 10)
    assert got["B"]["signal"] == "HOLD" and got["N"]["label"] == "신규 편입" and got["N"]["change_pct"] is None
    assert (got["S"]["signal"], got["S"]["label"], got["S"]["value"], got["S"]["flow"]) == ("SELL", "전량 매도", 0, -80)
    assert round(sum(p["weight"] for p in got.values())) == 100


F4 = """<?xml version="1.0"?><ownershipDocument>
 <issuer><issuerCik>0000797468</issuerCik><issuerName>OCCIDENTAL PETROLEUM CORP</issuerName><issuerTradingSymbol>OXY</issuerTradingSymbol></issuer>
 <reportingOwner><reportingOwnerId><rptOwnerCik>0001067983</rptOwnerCik></reportingOwnerId></reportingOwner>
 <nonDerivativeTable>
  <nonDerivativeTransaction><transactionCoding><transactionCode>P</transactionCode></transactionCoding>
   <transactionAmounts><transactionShares><value>1000000</value></transactionShares><transactionPricePerShare><value>46.00</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction>
  <nonDerivativeTransaction><transactionCoding><transactionCode>P</transactionCode></transactionCoding>
   <transactionAmounts><transactionShares><value>250000</value></transactionShares><transactionPricePerShare><value>46.50</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction>
 </nonDerivativeTable></ownershipDocument>"""


def test_parse_form4_buy_and_issuer_filter():
    ev = parse_form4(F4, 1067983)
    assert ev["signal"] == "BUY" and ev["ticker"] == "OXY" and ev["detail"] == "1,250,000주 매수 · 평균 $46.10"
    assert parse_form4(F4, 797468) is None      # 투자자가 발행사인 신고
    assert parse_form4(F4, 1336528) is None     # 투자자가 보고자가 아님


IDX = """<div class="companyInfo"><span class="companyName">UBER TECHNOLOGIES, INC (Subject)
 <acronym title="Central Index Key">CIK</acronym>: <a href="/cgi-bin/browse-edgar?CIK=0001543151">0001543151 (see all company filings)</a></span></div>
<div class="companyInfo"><span class="companyName">PERSHING SQUARE CAPITAL MANAGEMENT, L.P. (Filed by)
 <acronym title="Central Index Key">CIK</acronym>: <a href="/cgi-bin/browse-edgar?CIK=0001336528">0001336528 (see all company filings)</a></span></div>"""


def test_parse_index_parties_and_event():
    p = parse_index_parties(IDX)
    assert p["Subject"] == {"name": "Uber Technologies, Inc", "cik": 1543151} and p["Filed by"]["cik"] == 1336528
    assert event_from_13dg("SC 13D", p["Subject"])["title"] == "Uber Technologies, Inc 5% 이상 지분 취득(경영 참여 가능)"
    assert event_from_13dg("SCHEDULE 13G/A", p["Subject"])["signal"] is None
