# 마켓 레이더 · Market Radar

(옛 이름: 웨일 레이더) 버핏·애크먼·버리 등 **거물 투자자의 SEC 공시와 인터뷰·뉴스**, 그리고 **미국·중국·인도 ETF 등락 순위**를 매일 한국어로 모아 보는 앱입니다.
https://dkwo284-max.github.io/Whale_Radar/

| 화면 | 내용 |
|---|---|
| 홈 | 오늘의 브리핑(가장 큰 순매수 종목·가장 많이 겹친 종목·최근 새 소식), 섹터 흐름, 거물 컨센서스, 최근 인터뷰·뉴스 |
| 투자자 | 투자자별 13F 보유 종목, 섹터 비중, 이번 분기 큰 변화, BUY·HOLD·SELL |
| 공시 | 13F·Form 4(장내 매매)·13D/G(5% 이상 지분) 타임라인 |
| ETF | 미국·중국·인도 ETF 상승/하락 Top 20 (1일·7일·14일·30일·90일·180일·YTD·1년 전환), 어제 대비 순위 변동(▲▼·NEW), 기간별 히트맵, 테마별 흐름, Claude 한국어 해설, ETF 상세(1년 추이·운용사·AUM·보수·보유종목) |
| 글로벌 | 패권 판정(10개 지표·경고선), 패권 이동 지도: 나라별 대표 ETF 의 미국(SPY) 대비 상대수익·추월 배지, 패권 레이더 지역 신호×시장 반응 분면, 테마×지역 기회 지도(큰손 13F 포함), 규칙 기반 관찰 후보(근거·반대 근거·리스크), 달러·금·금리·환율과 뉴질랜드달러 기준 보기 |
| 뉴스 | 인터뷰·뉴스·서한을 AI 가 한국어 제목·3줄 요약·종목 논조로 정리 |
| 관심 | 관심 투자자·종목·키워드만 모아 보기 (이 기기에만 저장) |

## 동작 방식

- `.github/workflows/update.yml` 이 매일 UTC 03:37 — SEC 접수 마감(미국 동부 밤 10시) 직후, 동부시간 밤 10:37(표준시)·11:37(서머타임) — 실행 → `scripts/build.py`
  - **SEC EDGAR**(키 불필요): 투자자별 최근 13F 두 분기 비교(±5% 기준 BUY/SELL), 최근 180일 Form 4·13D/G
  - 13F 의 CUSIP → 티커는 **OpenFIGI**(키 불필요), 섹터는 SEC 업종코드(SIC)로 근사. 결과는 `cache/` 에 쌓아 다시 묻지 않음
  - **Google 뉴스 RSS** → **Gemini 무료 등급**으로 한국어 요약(새 기사만, 하루 최대 40건을 요청 1회로. 하루 첫 실행에서 AI 를 쓰고, 같은 날 다른 실행은 새 기사가 `news.min_new_extra_run`(6)건 이상일 때만 다시 씀 — 건너뛴 기사는 다음 실행으로 이월, 내 보장량 3회 + 공유 예비분, 하루는 오클랜드 00시 기준 — `web/data/ai-usage.json`, `scripts/budget.py`)
- 결과는 `web/data/dashboard.json`, `web/data/news.json` 에 커밋되고 `web/` 폴더가 GitHub Pages 로 배포됩니다.

### ETF 등락 (`.github/workflows/etf.yml` → `scripts/build_etf.py`, 설정 `etf.yaml`)

- 매일 UTC 21:13(미국장 마감 뒤, 뉴질랜드 오전 9~10시대) 실행. 키 불필요
  - **미국 상장**: 나스닥 심볼 목록(nasdaqtraded.txt)의 모든 ETF → 이름으로 나라 분류(미국 / 중국 / 인도, 그 밖의 나라는 제외)
  - **현지 상장**: `etf.yaml` 의 중국(상해·심천·홍콩)·인도(NSE) 목록 — 한 줄 추가로 늘릴 수 있음
  - 시세는 **Yahoo Finance(yfinance)** 수정종가. 기간 수익률과 "하루 전 기준" 수익률을 함께 저장해 화면에서 어제 대비 순위 변동을 계산
  - **제외**: 레버리지·인버스·VIX(이름 규칙 + `exclude` 목록), 20일 평균 거래대금이 작은 ETF(통화별 기준), 운용자산 $50M 미만, 상장 20거래일 미만
  - 순위에 오를 후보만 운용사·AUM·보수·보유종목을 받아 `cache/etf_info.json` 에 14일 보관
- **한국어 해설**: 매일 오전 11시(오클랜드) Claude 예약 작업이 `web/data/etf.json` 을 읽고 상·하위 ETF 의 원인을 뉴스로 찾아 `web/notes/etf-notes.json` 에 써서 푸시 → `update.yml` 의 push 배포로 반영

### 글로벌 (`scripts/build_macro.py` + `scripts/macro.py`, 설정 `macro.yaml`)
- **수집**: ETF 수집과 같은 Actions 실행(`etf.yml`)에서 나라·지역 ETF, 환율, 금·구리·원유·미국 10년 금리를 Yahoo Finance 로 받아 `web/data/macro.json` 을 만듭니다. 실패해도 ETF 순위 배포는 그대로 진행됩니다.
- **패권 신호**: `web/data/hegemony.json` — 패권 재편 레이더(Claude artifact)의 지역별 ▲◆▼ 칸. 매주 월요일 Claude 예약 작업("마켓 레이더 글로벌 주간 갱신·해설")이 artifact 에서 옮겨 쓰고, 이 파일이 바뀌면 Actions 가 점수만 다시 계산합니다.
- **패권 판정·근거 지표·2050 배지**: 같은 `hegemony.json` 의 `indicators`(10개 지표 신호), `thresholds`(경고선), `gdp2050`(Goldman Sachs·PwC 2050 순위)를 화면이 직접 읽습니다. ⓪ 패권 판정 카드, ③ 테마별 '근거 지표', ⑤ 달러인덱스·10년 금리 칸의 경고선(실시간 시세로 판정), 나라 줄의 '2050 ▲▼' 배지, ② 지역 점수 주간 추이(`log`)에 쓰입니다. 시장 숫자는 macro.json, 판단은 hegemony.json 으로 나눠 겹치지 않게 했습니다.
- **관찰 후보 규칙**: ① 지역 신호 점수 ≥ +1(금은 미국 통화·금융 ▼) ② 미국 대비 90일 수익 > 0 ③ 큰손 13F 매수 > 매도 또는 거래대금 20일/120일 ≥ 1.15 ④ 20일 평균 거래대금 ≥ $5M. 4개 통과 = 후보, 3개 = 관찰 대기. 기준값은 `macro.yaml` 의 `thresholds`.
- **해설**: 같은 주간 작업이 `web/notes/macro-notes.json`(요약·후보별 한 줄)을 씁니다. 매수 추천이 아닌 참고 정보입니다.

## 처음 설정

1. Settings → Pages → Source 를 **GitHub Actions** 로
2. Settings → Secrets and variables → Actions 에
   - `GEMINI_API_KEY`: Gemini 키 (Daily Brief 와 같은 키를 써도 되지만 무료 한도를 함께 씁니다)
   - `SEC_USER_AGENT`(선택): `이름 이메일` 형식. SEC 는 연락처가 있는 요청 헤더를 권장합니다
3. Actions → Market Radar update (투자자·뉴스) → Run workflow, Actions → Market Radar ETF update → Run workflow

## 13F 시즌·종목 중심·대가 비교 (투자자 화면 보강)

- **13F 시즌 카드**(홈): 가장 최근 분기의 마감(분기 말 + 45일)과 새 공시가 도착한 대가(신규 편입·전량 매도·비중 급변 종목), 아직 기다리는 대가를 보여줍니다(`dashboard.json` 의 `season`).
- **종목 중심 보기**(투자자 탭): 많이 보유·같이 산 종목·같이 판 종목·비중 합계 순위. **대가 비교**: 2~4명을 골라 평가액·집중도·최대 섹터·성적·겹치는 종목을 나란히.
- **상위 종목 비중 추이**: 최근 8개 분기 13F 에서 지금 상위 6종목의 비중 변화(`performance.json` 의 `hist`, 주 1회 성적 계산 때 함께).
- **공시 주체가 바뀐 대가**: `config.yaml` 에 새 `cik` 와 `prev_ciks: [이전 번호]` 를 적으면 직전 분기 비교와 성적 이력이 이어집니다(애크먼 → Pershing Square Inc., 아인혼 → DME Capital Management). 공시를 멈춘 대가는 `note` 로 이유를 표시합니다.
- **티커 보완**: OpenFIGI 가 못 찾는 해외 등록(CINS) 미국 상장 종목은 `scripts/meta.py` 의 `MANUAL` 표로 채웁니다.
- **뉴스 고르게**: 하루 AI 요약 한도(40건)에 앞쪽 대가만 들어가지 않게 대가별로 돌아가며 넣고, 최근 기사가 적은 대가를 먼저 넣습니다(`news.fair_order`).
- **데이터 상태**(화면 맨 아래): 공시·뉴스·성적·ETF·글로벌·해설의 마지막 갱신 시각(`web/data/status.json`, `scripts/status.py`).

## 알림 · 가벼운 첫 화면

- **알림(선택, 기본 꺼짐)**: 새 13F 가 도착하거나 새 Form 4·13D/G 가 올라오면 텔레그램·이메일로 알립니다(`scripts/notify.py`, 이전 수집과 비교해 새로 생긴 것만). `config.yaml` 의 `notify` 에서 `enabled: true` 로 켜고, 이 저장소 Settings → Secrets 에 `TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID`(또는 `SMTP_USER`·`SMTP_PASSWORD`·`EMAIL_TO`)를 넣으세요. `investors` 로 대가를, `forms` 로 공시 종류를 고릅니다.
- **첫 화면 데이터 줄이기**: 홈의 ETF 요약은 작은 `etf-home.json`(약 3KB)으로 그리고, 전체 `etf.json`(약 1.1MB)은 ETF 탭·검색·ETF 상세를 열 때만 받습니다. 성적 `performance.json`(약 0.4MB)은 투자자 탭을 처음 열 때 받습니다.

## 투자자 추가·변경

`config.yaml` 의 `investors` 에 `id·name·firm·cik·expect·news` 를 추가합니다. CIK 는 SEC EDGAR 회사 검색에서 찾을 수 있습니다.
실행할 때 SEC 등록 이름이 `expect` 와 다르면 앱 하단 경고에 표시됩니다.

## 로컬 실행

```bash
pip install -r requirements.txt pytest
python -m pytest -q
python scripts/build.py --only buffett,ackman --no-news   # 결과는 out/
python -m http.server -d web 8000
```

## 주의

- 13F 는 분기 종료 후 최대 45일 뒤 공개되는 지연 공시입니다. "지금 산다"가 아니라 "지난 분기에 샀다"는 뜻입니다.
- 추정 순매수 = 보유 수량 변화 × 분기말 주가. 실제 매매 가격과 다를 수 있습니다.
- 뉴스 요약·종목 논조는 AI 추정입니다. 투자 권유가 아닙니다.

## 🏆 대가 성적 (투자자 탭)

- **13F 복제 포트폴리오 추정 수익률**: 각 분기 말 13F 상위 30종목 비중으로 다음 분기 말까지 보유했다고 보고 분기 수익률을 이어 붙여 1·3·5년 수익률과 연평균을 계산합니다. S&P500(SPY, 배당 포함)과 비교합니다.
- **순위표**: 기간(1·3·5년)과 기준(분기말 기준 / 공시 보고 따라 샀다면)을 고르면 대가별 순위, S&P500 대비 초과수익, 시장을 이긴 분기 비율, 최대 낙폭이 나옵니다. 최근 13F 가 200일 넘게 없는 투자자는 순위에서 뺍니다.
- **분기별 히트맵**: 대가 × 최근 12분기 수익률(빨강 = S&P500보다 좋음, 파랑 = 나쁨).
- **대가별 상세**: 누적 수익 곡선(대가·따라 샀다면·S&P500), 1년·3년·5년, 따라 샀다면 1·3년, 시장 이긴 분기, 최대 낙폭, 연 변동성, 상위 10종목 비중, 분기 회전율, 분기별 수익에 가장 기여한 종목·깎아 먹은 종목.
- **한계**: 13F 는 분기 말 미국 주식 롱만 담습니다(현금·공매도·채권·해외·대부분의 옵션 제외, 분기 중 매매 안 보임). 실제 펀드 수익률이 아닌 추정치이며 수수료·세금은 빼지 않았습니다. 시세를 찾지 못한 종목(상장폐지 등)은 빼고 비중을 다시 나눕니다.
- **데이터**: `scripts/perf.py` → `web/data/performance.json`. 워크플로 `Investor performance` 가 매주 일요일(+수동) 계산합니다. 과거 13F 는 `cache/13f/` 에 저장해 새 분기만 받고, 시세는 yfinance. AI 를 쓰지 않습니다.
