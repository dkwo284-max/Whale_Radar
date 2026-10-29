"""투자자 관련 기사: Google 뉴스 RSS 로 모으고, Gemini 가 한국어 제목·3줄 요약·종목 논조(BUY/SELL/HOLD)를 붙인다.

- 이미 처리한 기사는 다시 AI 에 보내지 않는다(누적, keep_days 일 보관).
- 기사 본문은 저장하지 않는다. RSS 에는 보통 제목만 있어 요약 근거(basis)는 대부분 '제목'이다.
- 키가 없거나 한도를 넘으면 제목만 표시한다.
"""
from __future__ import annotations

import hashlib
import html
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote_plus

import feedparser
import httpx

from common import log
from llm import Gemini, LLMError

RSS = "https://news.google.com/rss/search?q={q}+when:{d}d&hl=en-US&gl=US&ceid=US:en"
SECTORS = ["IT", "커뮤니케이션", "경기소비재", "필수소비재", "헬스케어", "금융", "산업재", "에너지", "소재", "유틸리티", "부동산", "ETF·기타"]

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "STRING"},
                    "relevant": {"type": "BOOLEAN"},
                    "kind": {"type": "STRING", "enum": ["interview", "news", "letter"]},
                    "title_ko": {"type": "STRING"},
                    "summary": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "calls": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "ticker": {"type": "STRING"},
                                "stance": {"type": "STRING", "enum": ["BUY", "SELL", "HOLD"]},
                                "note": {"type": "STRING"},
                                "sector": {"type": "STRING", "enum": SECTORS},
                            },
                            "required": ["ticker", "stance", "note", "sector"],
                        },
                    },
                },
                "required": ["id", "relevant", "kind", "title_ko", "summary", "calls"],
            },
        }
    },
    "required": ["items"],
}

SYSTEM = """당신은 미국 투자 뉴스를 한국어로 정리하는 편집자입니다. 각 기사(id)마다:
- relevant: 이 기사가 지정된 투자자 본인(또는 그 펀드)의 발언·매매·보유·서한을 다루면 true. 이름만 스쳐 가거나 동명이인·다른 회사 기사면 false.
- kind: 투자자가 직접 말한 인터뷰·발언이면 "interview", 주주서한·분기 메모·연례 편지면 "letter", 그 외 보도는 "news".
- title_ko: 자연스러운 한국어 제목(45자 이내).
- summary: 핵심 1~3줄(각 60자 이내). 주어진 제목·설명에 있는 사실만. 없는 수치·날짜를 지어내지 마세요.
- calls: 기사에 나온 '이 투자자'의 종목 매수·매도·보유 의견이나 실제 매매만. 미국 티커(대문자)와 stance(BUY/SELL/HOLD),
  note(25자 이내 근거), sector(목록 중 하나). 근거가 없으면 빈 배열. 시장 전반 의견만 있으면 빈 배열.
모든 id 를 한 번씩 반환하세요."""


def item_id(link: str, title: str) -> str:
    return hashlib.sha1((link or title).encode("utf-8")).hexdigest()[:12]


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse_feed(raw: bytes | str, investor: str, lookback_days: int) -> list[dict]:
    feed = feedparser.parse(raw)
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    out = []
    for e in feed.entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        when = datetime(*t[:6], tzinfo=timezone.utc) if t else None
        if when and when < cutoff:
            continue
        title = _clean(e.get("title", ""))
        source = _clean((e.get("source") or {}).get("title", "")) if isinstance(e.get("source"), dict) else ""
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        desc = _clean(e.get("summary", ""))
        if desc.startswith(title):
            desc = desc[len(title):].strip(" -–")
        if desc == source:
            desc = ""
        out.append({"id": item_id(e.get("link", ""), title), "investor": investor, "title": title, "source": source,
                    "url": e.get("link", ""), "date": (when or datetime.now(timezone.utc)).date().isoformat(), "desc": desc})
    return out


def collect(cfg: dict, client: httpx.Client, known: set[str]) -> list[dict]:
    ncfg = cfg.get("news", {})
    fresh = []
    for inv in cfg["investors"]:
        url = RSS.format(q=quote_plus(inv["news"]), d=int(ncfg.get("lookback_days", 30)))
        try:
            r = client.get(url)
            r.raise_for_status()
            items = [x for x in parse_feed(r.content, inv["id"], int(ncfg.get("lookback_days", 30))) if x["id"] not in known]
        except Exception as e:  # noqa: BLE001
            log.warning("news %s failed: %s", inv["id"], e)
            continue
        seen_titles = set()
        for x in sorted(items, key=lambda x: x["date"], reverse=True):
            key = x["title"].lower()[:60]
            if key in seen_titles:
                continue
            seen_titles.add(key)
            fresh.append(x)
            if sum(1 for y in fresh if y["investor"] == inv["id"]) >= int(ncfg.get("per_investor", 6)):
                break
    return fresh


def summarize(items: list[dict], cfg: dict, llm: Gemini) -> tuple[list[dict], str | None, list[str]]:
    """AI 요약 → (실을 기사, 경고, AI 가 관련 없다고 본 기사 id). 실패하면 제목만."""
    names = {i["id"]: f"{i['name']} ({i['firm']})" for i in cfg["investors"]}
    if not items:
        return [], None, []
    if not llm.enabled:
        return [{**x, "ai": False, "basis": "제목", "summary": [], "calls": [], "kind": "news"} for x in items], "AI 키가 없어 요약 없이 제목만 표시합니다.", []
    limit = int(cfg.get("news", {}).get("max_ai_items", 40))
    todo, rest = items[:limit], items[limit:]
    rows: dict[str, dict] = {}
    warn = None
    for start in range(0, len(todo), 40):  # 보통 한 번에 (요청 1회)
        chunk = todo[start:start + 40]
        user = "\n\n".join(f"[id] {x['id']}\n[투자자] {names.get(x['investor'], x['investor'])}\n[제목] {x['title']}\n[출처] {x['source']} · {x['date']}"
                           + (f"\n[설명] {x['desc'][:400]}" if x["desc"] else "") for x in chunk)
        try:
            out = llm.json_call(SYSTEM, user, SCHEMA)
            rows.update({r.get("id"): r for r in out.get("items", [])})
        except LLMError as e:
            log.warning("news AI failed: %s", e)
            warn = "일부 기사는 AI 요약에 실패해 제목만 표시합니다."
    out_items, irrelevant = [], []
    for x in todo:
        r = rows.get(x["id"])
        if r is None:
            out_items.append({**x, "ai": False, "basis": "제목", "summary": [], "calls": [], "kind": "news"})
            continue
        if not r.get("relevant", True):
            irrelevant.append(x["id"])
            continue
        calls = [{"ticker": c["ticker"].upper().strip("$ ")[:6], "stance": c["stance"], "note": c.get("note", "")[:40],
                  "sector": c.get("sector", "ETF·기타")} for c in r.get("calls", []) if re.match(r"^\$?[A-Za-z][A-Za-z.\-]{0,5}$", c.get("ticker", ""))]
        out_items.append({**x, "ai": True, "basis": "발췌" if x["desc"] else "제목", "kind": r.get("kind", "news"),
                          "title_ko": r.get("title_ko", "")[:80], "summary": [s[:90] for s in r.get("summary", [])][:3], "calls": calls})
    # 한도를 넘어 다음 실행으로 미룬 기사는 저장하지 않는다(다음에 새 기사로 다시 잡힘)
    if rest:
        log.info("news: %d item(s) deferred to next run", len(rest))
    return out_items, warn, irrelevant


def update_news(cfg: dict, old: dict, llm: Gemini, client: httpx.Client | None = None) -> dict:
    own = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; WhaleRadar/1.0)"})
    try:
        items = {x["id"]: x for x in old.get("items", [])}
        fresh = collect(cfg, client, set(items) | set(old.get("dropped", [])))
        done, warn, dropped = summarize(fresh, cfg, llm)
    finally:
        if own:
            client.close()
    # 관련 없다고 본 기사는 다음 실행에서 다시 AI 에 보내지 않도록 id 만 기억
    for x in done:
        x.pop("desc", None)
        items[x["id"]] = x
    cutoff = (date.today() - timedelta(days=int(cfg.get("news", {}).get("keep_days", 120)))).isoformat()
    live = sorted((x for x in items.values() if x["date"] >= cutoff), key=lambda x: (x["date"], x["id"]), reverse=True)
    log.info("news: %d new, %d kept", len(done), len(live))
    return {"demo": False, "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "ai": "gemini" if llm.enabled else "none", "items": live,
            "dropped": (old.get("dropped", []) + dropped)[-2000:], "warnings": [warn] if warn else []}
