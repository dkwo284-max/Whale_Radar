from meta import sic_sector
from news import parse_feed, skip_ai_this_run, summarize

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Buffett's Berkshire trims Apple stake again - Reuters</title><link>https://news.google.com/a</link>
<pubDate>Mon, 26 Oct 2026 10:00:00 GMT</pubDate><description>&lt;a href="x"&gt;Buffett's Berkshire trims Apple stake again&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font&gt;Reuters&lt;/font&gt;</description>
<source url="https://reuters.com">Reuters</source></item>
<item><title>Old story - CNBC</title><link>https://news.google.com/b</link><pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate><source url="https://cnbc.com">CNBC</source></item>
</channel></rss>"""


def test_sic_sector():
    assert sic_sector(3571) == "IT" and sic_sector(2834) == "헬스케어" and sic_sector(1311) == "에너지"
    assert sic_sector(6022) == "금융" and sic_sector(6798) == "부동산" and sic_sector(None) == "ETF·기타"
    assert sic_sector(4911) == "유틸리티" and sic_sector(2080) == "필수소비재" and sic_sector(5812) == "경기소비재"


def test_parse_feed():
    items = parse_feed(RSS, "buffett", 30)
    assert len(items) == 1
    x = items[0]
    assert x["title"] == "Buffett's Berkshire trims Apple stake again" and x["source"] == "Reuters" and x["desc"] == ""


class FakeLLM:
    enabled = True

    def json_call(self, system, user, schema, max_tokens=16000):
        return {"items": [{"id": "a1", "relevant": True, "kind": "news", "title_ko": "버크셔, 애플 지분 또 축소",
                           "summary": ["애플 보유 수량을 줄였다."], "calls": [{"ticker": "$aapl", "stance": "SELL", "note": "지분 축소", "sector": "IT"}]},
                          {"id": "a2", "relevant": False, "kind": "news", "title_ko": "x", "summary": [], "calls": []}]}


def test_summarize_filters_irrelevant():
    cfg = {"investors": [{"id": "buffett", "name": "워런 버핏", "firm": "Berkshire"}], "news": {}}
    items = [{"id": "a1", "investor": "buffett", "title": "t", "source": "s", "url": "u", "date": "2026-10-26", "desc": ""},
             {"id": "a2", "investor": "buffett", "title": "t2", "source": "s", "url": "u2", "date": "2026-10-26", "desc": ""}]
    out, warn, dropped = summarize(items, cfg, FakeLLM())
    assert [x["id"] for x in out] == ["a1"] and dropped == ["a2"] and warn is None
    assert out[0]["calls"] == [{"ticker": "AAPL", "stance": "SELL", "note": "지분 축소", "sector": "IT"}] and out[0]["basis"] == "제목"


def test_skip_ai_only_for_small_extra_runs():
    assert skip_ai_this_run(0, False, 6) is False       # 새 기사가 없으면 애초에 AI 호출이 없음
    assert skip_ai_this_run(3, True, 6) is False        # 그날 첫 실행은 적어도 쓴다
    assert skip_ai_this_run(3, False, 6) is True        # 같은 날 추가 실행 + 새 기사 적음 → 건너뜀
    assert skip_ai_this_run(6, False, 6) is False       # 충분히 쌓였으면 다시 씀


def test_fair_order_round_robin_prefers_sparse_investors():
    import news
    fresh = [{"id": f"a{i}", "investor": "a"} for i in range(3)] + [{"id": "b0", "investor": "b"}]
    stored = [{"investor": "a", "date": "2999-01-01"}] * 5
    got = [x["id"] for x in news.fair_order(fresh, stored)]
    assert got == ["b0", "a0", "a1", "a2"]


def test_season_lists_arrived_and_waiting():
    from datetime import date
    import build
    inv = [{"id": "x", "period": "2026-09-30", "filed": "2026-11-10", "stale": False}, {"id": "y", "period": "2026-06-30", "filed": "2026-08-14", "stale": False},
           {"id": "z", "period": "2025-09-30", "filed": "2025-11-03", "stale": True}]
    s = build.season(inv, date(2026, 11, 10))
    assert s["period"] == "2026-09-30" and s["deadline"] == "2026-11-14" and s["days_left"] == 4
    assert [a["id"] for a in s["arrived"]] == ["x"] and s["waiting"] == ["y"]


def test_notify_diff_only_new_filings():
    import notify
    old = {"investors": [{"id": "a", "name": "A", "period": "2026-06-30"}, {"id": "b", "name": "B", "period": "2026-09-30"}],
           "timeline": [{"url": "u1", "investor": "a", "form": "Form 4", "title": "old"}]}
    new = {"investors": [{"id": "a", "name": "A", "period": "2026-09-30", "counts": {"BUY": 2, "SELL": 1},
                          "positions": [{"label": "신규 편입", "ticker": "NVDA", "value": 5}, {"label": "전량 매도", "ticker": "KO", "prev_value": 3}]},
                         {"id": "b", "name": "B", "period": "2026-09-30", "counts": {}, "positions": []},
                         {"id": "c", "name": "C", "period": "2026-09-30", "counts": {}, "positions": []}],
           "timeline": [{"url": "u1", "investor": "a", "form": "Form 4", "title": "old"}, {"url": "u2", "investor": "b", "form": "SC 13D/A", "title": "지분 변경", "ticker": "XYZ"},
                        {"url": "u3", "investor": "a", "form": "13F-HR", "title": "13F"}]}
    lines = notify.diff_lines(old, new)
    assert len(lines) == 2 and "A 2026 Q3 13F" in lines[0] and "신규 NVDA" in lines[0] and "전량 매도 KO" in lines[0] and "B SC 13D/A" in lines[1] and "(XYZ)" in lines[1]
    assert notify.diff_lines(old, new, only=["b"]) == [lines[1]]
    assert notify.notify(old, new, {"notify": {"telegram": {"enabled": False}}}) == 0
