"""대가 매매 흐름 요약: dashboard.json(13F 보유 변동·Form 4) + news.json → web/data/flows.json

흩어진 공시·뉴스를 '누가 무엇을 사고팔아 어디로 비중을 옮겼나'로 묶는다.
- 숫자·방향·한 줄 판정은 규칙으로 매일 계산 (AI 없음)
- 문단 해설은 13F 분기마다 한 번만 Gemini 로 생성(ai_notes), 실패해도 규칙 문장이 대신 보임
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

STYLES = [
    ("가치투자", ["buffett", "klarman", "lilu", "einhorn", "miller", "burry"]),
    ("행동주의", ["ackman", "icahn", "loeb"]),
    ("매크로", ["druckenmiller", "dalio", "tepper"]),
    ("성장·기술", ["wood", "glenview", "lightstreet", "viking"]),
]


def _usd(v: float) -> str:
    a = abs(v)
    s = f"${a / 1e9:.1f}B" if a >= 1e9 else f"${a / 1e6:.0f}M" if a >= 1e6 else f"${a / 1e3:.0f}K"
    return ("+" if v > 0 else "−" if v < 0 else "") + s


def _sector_weights(positions: list[dict], key: str) -> dict[str, float]:
    tot = sum(p.get(key) or 0 for p in positions)
    w: dict[str, float] = defaultdict(float)
    for p in positions:
        if p.get(key):
            w[p.get("sector") or "기타"] += p[key]
    return {s: v / tot * 100 for s, v in w.items()} if tot else {}


def investor_flow(inv: dict, timeline: list[dict], news: list[dict], today: date) -> dict:
    ps = inv.get("positions") or []
    prev_total = sum(p.get("prev_value") or 0 for p in ps)
    total = inv.get("total_value") or sum(p.get("value") or 0 for p in ps)
    buy = sum(p["flow"] for p in ps if (p.get("flow") or 0) > 0)
    sell = -sum(p["flow"] for p in ps if (p.get("flow") or 0) < 0)
    net = buy - sell
    base = prev_total or total or 1
    net_pct = net / base * 100
    turnover = (buy + sell) / base * 100
    if turnover < 3:
        tag = "관망"
    elif net_pct >= 3:
        tag = "적극 매수"
    elif net_pct <= -3:
        tag = "비중 축소"
    else:
        tag = "갈아타기"

    w_now, w_prev = _sector_weights(ps, "value"), _sector_weights(ps, "prev_value")
    secs = sorted(set(w_now) | set(w_prev), key=lambda s: -(w_now.get(s, 0)))
    sector_rows = [{"s": s, "prev": round(w_prev.get(s, 0), 1), "now": round(w_now.get(s, 0), 1),
                    "d": round(w_now.get(s, 0) - w_prev.get(s, 0), 1)} for s in secs]
    shifts = sorted([r for r in sector_rows if abs(r["d"]) >= 1], key=lambda r: -abs(r["d"]))[:2]

    def mv(p):
        return {"t": p.get("ticker") or p.get("issuer", "")[:12], "n": p.get("issuer", ""), "l": p.get("label", ""),
                "f": round(p.get("flow") or 0), "c": p.get("change_pct")}

    buys = [mv(p) for p in sorted(ps, key=lambda p: -(p.get("flow") or 0)) if (p.get("flow") or 0) > 0][:3]
    sells = [mv(p) for p in sorted(ps, key=lambda p: (p.get("flow") or 0)) if (p.get("flow") or 0) < 0][:3]
    new = [p.get("ticker") or p.get("issuer") for p in sorted(ps, key=lambda p: -(p.get("value") or 0)) if p.get("label") == "신규 편입"][:5]
    exits = [p.get("ticker") or p.get("issuer") for p in sorted(ps, key=lambda p: -(p.get("prev_value") or 0)) if p.get("label") == "전량 매도"][:5]

    bits = []
    if shifts:
        bits.append(" · ".join(f"{r['s']} {'+' if r['d'] > 0 else '−'}{abs(r['d']):.1f}%p" for r in shifts))
    if buys:
        bits.append("·".join(b["t"] for b in buys[:2]) + " 매수")
    if sells:
        bits.append("·".join(s["t"] for s in sells[:2]) + " 매도")
    bits.append(f"순{'매수' if net >= 0 else '매도'} {_usd(net).lstrip('+−')} (보유액 대비 {net_pct:+.1f}%)")
    line = f"{tag} — " + ", ".join(bits)

    since = (today - timedelta(days=45)).isoformat()
    f4 = [{"d": t["date"], "t": t.get("ticker"), "sig": t.get("signal"), "x": t.get("detail", ""), "u": t.get("url", "")}
          for t in timeline if t.get("investor") == inv["id"] and t.get("form") != "13F-HR" and t.get("date", "") >= since][:3]
    nsince = (today - timedelta(days=21)).isoformat()
    nw = [{"d": n.get("date"), "t": n.get("title_ko") or n.get("title"), "u": n.get("url"), "k": n.get("kind", ""),
           "s": (n.get("summary") or [""])[0] if isinstance(n.get("summary"), list) else (n.get("summary") or "")}
          for n in sorted(news, key=lambda n: n.get("date", ""), reverse=True)
          if n.get("investor") == inv["id"] and (n.get("date") or "") >= nsince][:2]
    return {"id": inv["id"], "name": inv.get("name"), "firm": inv.get("firm"), "stale": bool(inv.get("stale")),
            "period": inv.get("period"), "filed": inv.get("filed"), "tag": tag, "line": line,
            "buy": round(buy), "sell": round(sell), "net": round(net), "net_pct": round(net_pct, 1), "turnover": round(turnover, 1),
            "total": round(total), "prev_total": round(prev_total),
            "n_now": sum(1 for p in ps if (p.get("value") or 0) > 0), "n_prev": sum(1 for p in ps if (p.get("prev_value") or 0) > 0),
            "buys": buys, "sells": sells, "new": new, "exits": exits, "sectors": sector_rows[:8], "form4": f4, "news": nw}


def build_flows(dashboard: dict, news: dict | None, old: dict | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    invs = [i for i in dashboard.get("investors", [])]
    rows = [investor_flow(i, dashboard.get("timeline", []), (news or {}).get("items", []), today) for i in invs]
    live = [r for r in rows if not r["stale"]]
    period = max((r["period"] or "" for r in live), default="")

    # 업종별 순유입 (전체) · 대가 × 업종
    sec_net: dict[str, float] = defaultdict(float)
    heat: dict[str, dict[str, float]] = {}
    for inv in invs:
        if inv.get("stale"):
            continue
        base = sum(p.get("prev_value") or 0 for p in inv.get("positions", [])) or inv.get("total_value") or 1
        hs: dict[str, float] = defaultdict(float)
        for p in inv.get("positions", []):
            f = p.get("flow") or 0
            sec_net[p.get("sector") or "기타"] += f
            hs[p.get("sector") or "기타"] += f
        heat[inv["id"]] = {s: round(v / base * 100, 1) for s, v in hs.items()}
    sectors = sorted(({"s": s, "f": round(v)} for s, v in sec_net.items()), key=lambda r: -abs(r["f"]))[:9]
    hcols = [r["s"] for r in sectors[:8]]

    cons = dashboard.get("consensus", [])
    def c(o):
        return {"t": o.get("ticker") or o.get("issuer", "")[:10], "n": o.get("issuer", ""), "sec": o.get("sector", ""),
                "b": [x["investor"] for x in o.get("buyers", [])], "s": [x["investor"] for x in o.get("sellers", [])]}
    agree_buy = [c(o) for o in cons if len(o.get("buyers", [])) >= 3 and len(o.get("sellers", [])) <= 1][:6]
    agree_sell = [c(o) for o in cons if len(o.get("sellers", [])) >= 3 and len(o.get("buyers", [])) <= 1][:6]
    split = [c(o) for o in cons if len(o.get("buyers", [])) >= 2 and len(o.get("sellers", [])) >= 2][:6]

    byid = {r["id"]: r for r in rows}
    styles = []
    for name, ids in STYLES:
        mem = [byid[i] for i in ids if i in byid and not byid[i]["stale"]]
        if not mem:
            continue
        ss: dict[str, float] = defaultdict(float)
        for m in mem:
            for s, v in heat.get(m["id"], {}).items():
                ss[s] += v
        top_in = max(ss.items(), key=lambda kv: kv[1], default=(None, 0))
        top_out = min(ss.items(), key=lambda kv: kv[1], default=(None, 0))
        styles.append({"name": name, "ids": [m["id"] for m in mem], "avg": round(sum(m["net_pct"] for m in mem) / len(mem), 1),
                       "buyers": sum(1 for m in mem if m["net"] > 0), "n": len(mem),
                       "in": top_in[0] if top_in[1] > 0 else None, "out": top_out[0] if top_out[1] < 0 else None})

    nb, ns = sum(1 for r in live if r["tag"] == "적극 매수"), sum(1 for r in live if r["tag"] == "비중 축소")
    nsw, nh = sum(1 for r in live if r["tag"] == "갈아타기"), sum(1 for r in live if r["tag"] == "관망")
    inflow = [r for r in sectors if r["f"] > 0][:2]
    outflow = sorted([r for r in sectors if r["f"] < 0], key=lambda r: r["f"])[:2]
    q = f"{period[:4]}년 {int(period[5:7]) // 3}분기" if len(period) >= 7 else "최근 분기"
    parts = [f"{q} 13F 기준 추적 {len(live)}명 중 적극 매수 {nb}명, 비중 축소 {ns}명, 갈아타기 {nsw}명, 관망 {nh}명입니다."]
    if inflow or outflow:
        parts.append("돈이 가장 많이 들어간 업종은 " + (", ".join(f"{r['s']}({_usd(r['f'])})" for r in inflow) or "없음")
                     + ", 가장 많이 빠진 업종은 " + (", ".join(f"{r['s']}({_usd(r['f'])})" for r in outflow) or "없음") + "입니다.")
    if agree_buy:
        parts.append("여러 대가가 함께 산 종목은 " + "·".join(x["t"] for x in agree_buy[:3]) + "입니다.")
    if split:
        parts.append("사는 쪽과 파는 쪽이 갈린 종목은 " + "·".join(x["t"] for x in split[:3]) + "입니다.")
    out = {"generated_at": dashboard.get("generated_at"), "period": period, "lead": " ".join(parts),
           "counts": {"buy": nb, "sell": ns, "switch": nsw, "hold": nh, "n": len(live)},
           "sectors": sectors, "heat": {"cols": hcols, "rows": [{"id": r["id"], "name": r["name"], "v": [heat.get(r["id"], {}).get(s, 0) for s in hcols]} for r in live]},
           "agree_buy": agree_buy, "agree_sell": agree_sell, "split": split, "styles": styles,
           "investors": sorted(rows, key=lambda r: (r["stale"], -abs(r["net_pct"])))}
    # 이전 AI 해설은 같은 분기면 유지
    if old and (old.get("ai") or {}).get("period") == period:
        out["ai"] = old["ai"]
    return out


SCHEMA = {"type": "object", "properties": {
    "market": {"type": "string"},
    "investors": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "line": {"type": "string"}}, "required": ["id", "line"]}},
}, "required": ["market", "investors"]}


def ai_notes(flows: dict, llm) -> dict | None:
    """분기 1회 한국어 해설. 데이터에 있는 숫자·종목만 쓰게 한다."""
    import json as _json
    brief = {"period": flows["period"], "counts": flows["counts"], "sectors": flows["sectors"][:6],
             "agree_buy": [x["t"] for x in flows["agree_buy"]], "agree_sell": [x["t"] for x in flows["agree_sell"]],
             "split": [x["t"] for x in flows["split"]], "styles": flows["styles"],
             "investors": [{"id": r["id"], "name": r["name"], "tag": r["tag"], "line": r["line"], "net_pct": r["net_pct"],
                            "buys": [b["t"] for b in r["buys"]], "sells": [s["t"] for s in r["sells"]], "new": r["new"], "exits": r["exits"],
                            "news": [n["t"] for n in r["news"]], "form4": [f"{f['d']} {f['t']} {f['sig']}" for f in r["form4"]]}
                           for r in flows["investors"] if not r["stale"]]}
    system = ("당신은 미국 13F 공시를 한국어로 해설하는 애널리스트입니다. 입력 JSON에 있는 숫자·종목·업종만 사용하고, "
              "없는 내용을 지어내지 마세요. 매수·매도 추천이나 '사세요' 같은 표현은 금지입니다. 13F는 분기 말 기준이고 45일 늦게 공개된다는 한계를 염두에 두세요.")
    user = ("다음 데이터로 (1) market: 대가들의 전체 매매 흐름을 3~4문장으로 요약(어느 업종으로 돈이 이동했는지, 함께 산/판/엇갈린 종목, 스타일별 차이), "
            "(2) investors: 대가마다 한 문장(60자 안팎)으로 '무엇을 줄이고 무엇을 늘려 어떤 방향으로 가는지', 최근 뉴스·Form 4가 있으면 그 행동과 연결해 해석.\n"
            + _json.dumps(brief, ensure_ascii=False))
    out = llm.json_call(system=system, user=user, schema=SCHEMA, max_tokens=6000)
    if not out or not out.get("market"):
        return None
    return {"period": flows["period"], "market": out["market"].strip(),
            "investors": {x["id"]: x["line"].strip() for x in out.get("investors", []) if x.get("id") and x.get("line")}}
