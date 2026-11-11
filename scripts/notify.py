"""알림(선택): 새 13F 도착·새 Form 4/13D/G 를 텔레그램·이메일로. 비밀값은 환경변수(GitHub Secrets)에서만 읽는다.

config.yaml 의 notify 에서 켠다(기본 꺼짐). 이전 dashboard.json 과 새 것을 비교해 '새로 생긴 것'만 보낸다 — 보낼 게 없으면 아무것도 보내지 않는다.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

import httpx

from common import log


def _quarter(iso: str) -> str:
    return f"{iso[:4]} Q{(int(iso[5:7]) + 2) // 3}" if iso else "—"


def diff_lines(old: dict, new: dict, only: list[str] | None = None, forms: list[str] | None = None, limit: int = 10) -> list[str]:
    """이전·새 dashboard 를 비교해 알릴 줄 목록. only: 대가 id 목록(비면 전체), forms: '13F'·'4'·'13DG' 중 알릴 종류."""
    forms = forms or ["13F", "4", "13DG"]
    want = (lambda i: not only or i in only)
    names = {i["id"]: i["name"] for i in new.get("investors", [])}
    old_period = {i["id"]: i.get("period") for i in old.get("investors", [])}
    lines: list[str] = []
    if "13F" in forms:
        for inv in new.get("investors", []):
            if not want(inv["id"]) or not inv.get("period") or inv["id"] not in old_period or (old_period[inv["id"]] or "") >= inv["period"]:
                continue  # 처음 추가된 대가는 알리지 않는다(이전 기준이 없음)
            ps = inv.get("positions", [])
            top = lambda label, key: [p["ticker"] for p in sorted((p for p in ps if p.get("label") == label and p.get("ticker")), key=lambda p: -p.get(key, 0))[:3]]  # noqa: E731
            nw, ex = top("신규 편입", "value"), top("전량 매도", "prev_value")
            c = inv.get("counts", {})
            lines.append(f"📥 {inv['name']} {_quarter(inv['period'])} 13F — 매수 {c.get('BUY', 0)} · 매도 {c.get('SELL', 0)}"
                         + (f" · 신규 {', '.join(nw)}" if nw else "") + (f" · 전량 매도 {', '.join(ex)}" if ex else ""))
    seen = {t.get("url") for t in old.get("timeline", [])}
    group = lambda f: "13F" if f.startswith("13F") else "4" if f.startswith("Form 4") else "13DG"  # noqa: E731
    for t in new.get("timeline", []):
        if t.get("url") in seen or not want(t.get("investor")) or group(t.get("form", "")) == "13F" or group(t.get("form", "")) not in forms:
            continue
        lines.append(f"📄 {names.get(t['investor'], t['investor'])} {t['form']} — {t.get('title', '')}" + (f" ({t['ticker']})" if t.get("ticker") else ""))
    return lines[:limit]


def send_telegram(text: str) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log.warning("telegram enabled but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set")
        return
    r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat, "text": text, "disable_web_page_preview": "true"}, timeout=20)
    if r.status_code >= 400:  # raise_for_status() 메시지에는 토큰이 든 URL 이 찍히므로 직접 처리
        raise RuntimeError(f"telegram HTTP {r.status_code}: {r.text[:200]}")
    log.info("telegram sent")


def send_email(subject: str, text: str) -> None:
    user, pw, to = (os.environ.get(k) for k in ("SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"))
    if not (user and pw and to):
        log.warning("email enabled but SMTP_USER/SMTP_PASSWORD/EMAIL_TO not set")
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(text)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, pw)
        s.send_message(msg)
    log.info("email sent")


def strategy_lines(old: dict, new: dict) -> list[str]:
    """추천 조합이 새 분기 13F 로 다시 짜였을 때만: 추천 조합 이름과 들어온·빠진 종목."""
    now = (new.get("bases", {}).get("follow") or {}).get("now") or {}
    was = (old.get("bases", {}).get("follow") or {}).get("now") or {}
    pick = (new.get("recommend") or {}).get("pick")
    if not now.get("period") or not was.get("period") or was["period"] >= now["period"] or not pick:
        return []
    ch = (now.get("changes") or {}).get(pick) or {}
    names = lambda rows: ", ".join(r["t"] for r in rows[:8]) + (f" 외 {len(rows) - 8}" if len(rows) > 8 else "")  # noqa: E731
    top = ", ".join(f"{h['t']} {h['w'] * 100:.0f}%" for h in (now.get("portfolios") or {}).get(pick, [])[:5])
    lines = [f"🧭 추천 조합 재조정 — {(new.get('labels') or {}).get(pick, pick)} ({_quarter(now['period'])} 13F 반영)"]
    if ch.get("in"):
        lines.append(f"➕ 새로 편입: {names(ch['in'])}")
    if ch.get("out"):
        lines.append(f"➖ 제외: {names(ch['out'])}")
    if top:
        lines.append(f"상위 비중: {top}")
    return lines


def notify_strategy(old: dict, new: dict, cfg: dict) -> int:
    n = cfg.get("notify") or {}
    tg, em = (n.get("telegram") or {}).get("enabled"), (n.get("email") or {}).get("enabled")
    if not (tg or em) or "strategy" not in (n.get("forms") or ["strategy"]):
        return 0
    lines = strategy_lines(old, new)
    if not lines:
        return 0
    text = "\n".join(["🐋 마켓 레이더"] + lines + [cfg.get("app_url", "")])
    for name, on, fn in (("telegram", tg, lambda: send_telegram(text)), ("email", em, lambda: send_email("마켓 레이더 — 추천 조합 재조정", text))):
        if not on:
            continue
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            log.error("%s notify failed: %s", name, e)
    return len(lines)


def notify(old: dict, new: dict, cfg: dict) -> int:
    n = cfg.get("notify") or {}
    tg, em = (n.get("telegram") or {}).get("enabled"), (n.get("email") or {}).get("enabled")
    if not (tg or em) or not old.get("investors"):
        return 0
    lines = diff_lines(old, new, n.get("investors") or None, n.get("forms") or None)
    if not lines:
        return 0
    text = "\n".join(["🐋 마켓 레이더 — 새 공시"] + lines + [cfg.get("app_url", "")])
    for name, on, fn in (("telegram", tg, lambda: send_telegram(text)), ("email", em, lambda: send_email("마켓 레이더 — 새 공시", text))):
        if not on:
            continue
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — 알림 실패가 수집을 막지 않게
            log.error("%s notify failed: %s", name, e)
    return len(lines)
