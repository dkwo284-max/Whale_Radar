# 웨일 레이더 · Whale Radar

버핏·애크먼·버리 등 **거물 투자자의 SEC 공시와 인터뷰·뉴스를 매일 한국어로** 모아 보는 앱입니다.
https://dkwo284-max.github.io/Whale_Radar/

| 화면 | 내용 |
|---|---|
| 홈 | 오늘의 브리핑(가장 큰 순매수 종목·가장 많이 겹친 종목·최근 새 소식), 섹터 흐름, 거물 컨센서스, 최근 인터뷰·뉴스 |
| 투자자 | 투자자별 13F 보유 종목, 섹터 비중, 이번 분기 큰 변화, BUY·HOLD·SELL |
| 공시 | 13F·Form 4(장내 매매)·13D/G(5% 이상 지분) 타임라인 |
| 뉴스 | 인터뷰·뉴스·서한을 AI 가 한국어 제목·3줄 요약·종목 논조로 정리 |
| 관심 | 관심 투자자·종목·키워드만 모아 보기 (이 기기에만 저장) |

## 동작 방식

- `.github/workflows/update.yml` 이 매일 UTC 03:37 — SEC 접수 마감(미국 동부 밤 10시) 직후, 동부시간 밤 10:37(표준시)·11:37(서머타임) — 실행 → `scripts/build.py`
  - **SEC EDGAR**(키 불필요): 투자자별 최근 13F 두 분기 비교(±5% 기준 BUY/SELL), 최근 180일 Form 4·13D/G
  - 13F 의 CUSIP → 티커는 **OpenFIGI**(키 불필요), 섹터는 SEC 업종코드(SIC)로 근사. 결과는 `cache/` 에 쌓아 다시 묻지 않음
  - **Google 뉴스 RSS** → **Gemini 무료 등급**으로 한국어 요약(새 기사만, 하루 최대 40건을 요청 1회로, 하루 상한 4회 — `web/data/ai-usage.json`)
- 결과는 `web/data/dashboard.json`, `web/data/news.json` 에 커밋되고 `web/` 폴더가 GitHub Pages 로 배포됩니다.

## 처음 설정

1. Settings → Pages → Source 를 **GitHub Actions** 로
2. Settings → Secrets and variables → Actions 에
   - `GEMINI_API_KEY`: Gemini 키 (Daily Brief 와 같은 키를 써도 되지만 무료 한도를 함께 씁니다)
   - `SEC_USER_AGENT`(선택): `이름 이메일` 형식. SEC 는 연락처가 있는 요청 헤더를 권장합니다
3. Actions → Whale Radar update → Run workflow

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
