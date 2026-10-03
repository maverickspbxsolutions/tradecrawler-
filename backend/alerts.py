"""Email alerts via Emergent-managed Resend. De-duplication + rate-limiting.
SMS deferred (architecture ready via settings.sms_enabled)."""
import os
import re
import ipaddress
import logging
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta
import httpx

from db import db

logger = logging.getLogger(__name__)

EMAIL_BASE_URL = "https://integrations.emergentagent.com"
EMAIL_KEY = os.environ["EMERGENT_EMAIL_KEY"]
EMAIL_FROM_NAME = os.environ["EMAIL_FROM_NAME"]

_SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "goo.gl", "rebrand.ly")
_CRED_ASK = ("reply with your password", "reply with the code", "send your password", "cvv",
             "send us your password", "enter your password below", "confirm your card number",
             "your full card number", "seed phrase", "recovery phrase", "verify your card",
             "social security number", "confirm your bank details")
_HOSTISH = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)


def _host_ok(host: str) -> bool:
    if not host or "xn--" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return not any(host == s or host.endswith("." + s) for s in _SHORTENERS)


def _same_site(shown: str, real: str) -> bool:
    return shown == real or real.endswith("." + shown) or shown.endswith("." + real)


class _EmailScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.urls, self.anchors = set(), [], []
        self._href, self._text = None, []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag.lower())
        self.urls += [v for k, v in attrs if k.lower() in ("href", "src") and v]
        if tag.lower() == "a":
            self._href = dict((k.lower(), v) for k, v in attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text)))
            self._href, self._text = None, []


def _assert_safe_email(subject: str, html: str) -> None:
    scan = _EmailScan()
    scan.feed(html)
    if scan.tags & {"form", "input", "textarea", "select"}:
        raise ValueError("No forms or input fields in email (G2)")
    body = f"{subject}\n{html}".lower()
    for p in _CRED_ASK:
        if p in body:
            raise ValueError(f"Email asks the recipient for credentials: {p!r} (G2)")
    for url in scan.urls:
        low = url.strip().lower()
        if low.startswith(("mailto:", "tel:", "cid:", "#")):
            continue
        if not low.startswith("https://"):
            raise ValueError(f"Email links/assets must be absolute https: {url!r} (G3)")
        host = urlparse(low).hostname or ""
        if not _host_ok(host) or urlparse(low).username is not None:
            raise ValueError(f"Shortened, numeric-host or credential-bearing URL: {url!r} (G3)")
    for href, text in scan.anchors:
        real = urlparse(href.strip().lower()).hostname or ""
        if not real:
            continue
        for m in _HOSTISH.finditer(text):
            if not _same_site(m.group(1).lower(), real):
                raise ValueError(f"Anchor text {m.group(1)!r} != real link host {real!r} (G3)")


async def send_email(*, to: str, subject: str, html: str) -> str | None:
    _assert_safe_email(subject, html)
    payload = {"to": [to], "subject": subject, "html": html, "from_name": EMAIL_FROM_NAME}
    reply_to = os.environ.get("EMAIL_REPLY_TO")
    if reply_to:
        payload["contact_email"] = reply_to
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{EMAIL_BASE_URL}/api/v1/email/send",
            headers={"X-Email-Key": EMAIL_KEY},
            json=payload,
        )
    resp.raise_for_status()
    return resp.json().get("id")


def _signal_html(signal: dict, strategy_name: str) -> str:
    side = signal["side"]
    color = "#00E676" if side == "BUY" else "#FF3366"
    price = signal.get("price", 0)
    return (
        f'<table role="presentation" width="100%" style="background:#0A0A0C;padding:24px">'
        f'<tr><td style="font-family:Arial,sans-serif;color:#EDEDED">'
        f'<p style="font-size:12px;letter-spacing:2px;color:#8A8A93;text-transform:uppercase">'
        f'{escape(EMAIL_FROM_NAME)} &middot; Signal Alert</p>'
        f'<h1 style="color:{color};margin:8px 0;font-size:28px">{escape(side)} {escape(signal["symbol"])}</h1>'
        f'<p style="font-size:16px">Strategy: <strong>{escape(strategy_name)}</strong></p>'
        f'<p style="font-size:16px">Price: <strong>${price:,.4f}</strong> &middot; Timeframe: {escape(signal.get("timeframe",""))}</p>'
        f'<p style="font-size:14px;color:#8A8A93">Reason: {escape(signal.get("reason",""))}</p>'
        f'<p style="font-size:14px;color:#8A8A93">Candle: {escape(str(signal.get("candle_time","")))}</p>'
        f'<div style="background:#FFE600;color:#000;padding:10px;font-size:11px;'
        f'font-weight:bold;letter-spacing:1px;margin-top:16px;text-align:center">'
        f'SIGNALS ARE NOT FINANCIAL ADVICE. PAPER-TRADING ONLY, NO ORDERS PLACED.</div>'
        f'<p style="font-size:11px;color:#565660;margin-top:16px">Sent by {escape(EMAIL_FROM_NAME)}. '
        f'We never ask for your password or payment details by email.</p>'
        f'</td></tr></table>'
    )


async def get_settings() -> dict:
    s = await db.settings.find_one({"id": "global"}, {"_id": 0})
    if not s:
        s = {
            "id": "global",
            "alert_email": "",
            "email_enabled": True,
            "sms_enabled": False,
            "max_alerts_per_hour": 10,
            "max_emails_per_day": 50,
            "min_minutes_between_same_strategy": 15,
        }
        await db.settings.insert_one(dict(s))
    return s


async def maybe_send_alert(signal: dict, strategy: dict) -> dict:
    """Apply gating, dedup and rate-limits. Returns dict describing what happened."""
    result = {"emailed": False, "reason": None}
    settings = await get_settings()

    if not strategy.get("live") or not strategy.get("gate_passed"):
        result["reason"] = "strategy not live / gate not passed"
        return result
    if not settings.get("email_enabled") or not settings.get("alert_email"):
        result["reason"] = "email disabled or no recipient set"
        return result

    now = datetime.now(timezone.utc)

    # idempotency: same signal never fires twice
    existing = await db.alerts.find_one({"idempotency_key": signal["sig_key"]})
    if existing:
        result["reason"] = "duplicate (idempotent)"
        return result

    # per-strategy cooldown
    cooldown = settings.get("min_minutes_between_same_strategy", 15)
    cutoff = (now - timedelta(minutes=cooldown)).isoformat()
    recent_same = await db.alerts.count_documents(
        {"strategy_id": strategy["id"], "sent_at": {"$gte": cutoff}})
    if recent_same > 0:
        result["reason"] = f"strategy cooldown ({cooldown}m)"
        return result

    # global hourly rate limit
    hour_cutoff = (now - timedelta(hours=1)).isoformat()
    hourly = await db.alerts.count_documents({"sent_at": {"$gte": hour_cutoff}})
    if hourly >= settings.get("max_alerts_per_hour", 10):
        result["reason"] = "hourly rate limit reached"
        return result

    # daily email cap
    day_cutoff = (now - timedelta(days=1)).isoformat()
    daily = await db.alerts.count_documents({"channel": "email", "sent_at": {"$gte": day_cutoff}})
    if daily >= settings.get("max_emails_per_day", 50):
        result["reason"] = "daily email cap reached"
        return result

    try:
        subject = f"[{signal['side']}] {signal['symbol']} - {strategy['name']}"
        html = _signal_html(signal, strategy["name"])
        email_id = await send_email(to=settings["alert_email"], subject=subject, html=html)
        await db.alerts.insert_one({
            "idempotency_key": signal["sig_key"],
            "strategy_id": strategy["id"],
            "signal_id": signal.get("id"),
            "channel": "email",
            "to": settings["alert_email"],
            "email_id": email_id,
            "sent_at": now.isoformat(),
        })
        result["emailed"] = True
        result["reason"] = "sent"
    except Exception as e:  # noqa
        logger.error(f"Alert email failed: {e}")
        result["reason"] = f"send error: {e}"
    return result
