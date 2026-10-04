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

# Direct Resend integration (independent of Emergent) — uses the user's own key.
RESEND_URL = "https://api.resend.com/emails"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "TradeCrawler")
EMAIL_FROM = f"{EMAIL_FROM_NAME} <{RESEND_FROM_EMAIL}>"
EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO")

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


async def send_email(*, to, subject: str, html: str) -> str | None:
    _assert_safe_email(subject, html)
    key = RESEND_API_KEY or os.environ.get("RESEND_API_KEY")
    if not key:
        raise RuntimeError("Email isn't set up yet — add RESEND_API_KEY to your deployment secrets.")
    recipients = to if isinstance(to, list) else [to]
    from_email = os.environ.get("RESEND_FROM_EMAIL", RESEND_FROM_EMAIL)
    from_name = os.environ.get("EMAIL_FROM_NAME", EMAIL_FROM_NAME)
    payload = {"from": f"{from_name} <{from_email}>", "to": recipients, "subject": subject, "html": html}
    if EMAIL_REPLY_TO:
        payload["reply_to"] = EMAIL_REPLY_TO
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
        )
    if resp.status_code >= 400:
        try:
            msg = resp.json().get("message") or resp.text
        except Exception:  # noqa
            msg = resp.text
        raise RuntimeError(msg or f"Resend returned {resp.status_code}")
    return resp.json().get("id")


def _signal_html(signal: dict, strategy_name: str) -> str:
    side = signal["side"]
    color = "#00E676" if side == "BUY" else "#FF3366"
    price = signal.get("price", 0)
    coin = signal["symbol"].replace("USDT", "")
    plan = signal.get("plan") or {}

    if side == "BUY":
        tp_pct = plan.get("target_pct", 0)
        sl_pct = plan.get("stop_pct", 0)
        hold = plan.get("hold_label")
        rows = ""
        for c in (20, 50, 100):
            prof = c * tp_pct / 100.0
            loss = c * sl_pct / 100.0
            rows += (
                f'<tr>'
                f'<td style="padding:7px 10px;border-top:1px solid #1F1F24;font-size:14px">${c}</td>'
                f'<td style="padding:7px 10px;border-top:1px solid #1F1F24;font-size:14px;color:#00E676;text-align:right">+${prof:,.2f}</td>'
                f'<td style="padding:7px 10px;border-top:1px solid #1F1F24;font-size:14px;color:#FF3366;text-align:right">-${abs(loss):,.2f}</td>'
                f'</tr>'
            )
        hold_html = (
            f'<p style="font-size:15px;margin:2px 0">&#9201; How long to stay in: '
            f'<strong>{escape(str(hold))}</strong> (based on how long this setup usually takes to play out).</p>'
            if hold else "")
        plan_html = (
            f'<p style="font-size:16px;margin:14px 0 4px"><strong>What to do now:</strong></p>'
            f'<p style="font-size:15px;margin:2px 0"><strong>Buy</strong> {escape(coin)} at around '
            f'<strong>${price:,.4f}</strong>.</p>'
            f'<p style="font-size:15px;margin:2px 0">Take-profit (TP): sell near '
            f'<strong style="color:#00E676">${plan.get("target_price",0):,.4f}</strong> (about +{tp_pct}%).</p>'
            f'<p style="font-size:15px;margin:2px 0">Stop-loss (SL): if it drops to '
            f'<strong style="color:#FF3366">${plan.get("stop_price",0):,.4f}</strong> (about {sl_pct}%), '
            f'sell to limit the loss.</p>'
            f'{hold_html}'
            f'<p style="font-size:16px;margin:16px 0 6px"><strong>What you could make or risk:</strong></p>'
            f'<table role="presentation" width="100%" style="border-collapse:collapse;background:#121215;border-radius:6px">'
            f'<tr>'
            f'<td style="padding:7px 10px;font-size:11px;color:#8A8A93;text-transform:uppercase;letter-spacing:1px">If you put in</td>'
            f'<td style="padding:7px 10px;font-size:11px;color:#8A8A93;text-transform:uppercase;letter-spacing:1px;text-align:right">If TP hits</td>'
            f'<td style="padding:7px 10px;font-size:11px;color:#8A8A93;text-transform:uppercase;letter-spacing:1px;text-align:right">If SL hits</td>'
            f'</tr>'
            f'{rows}'
            f'</table>'
            f'<p style="font-size:12px;color:#8A8A93;margin-top:6px">Estimates from this crawler&#39;s past '
            f'winning and losing trades &mdash; real results vary. Figures are before trading fees.</p>'
            f'<p style="font-size:13px;color:#8A8A93;margin-top:8px">The crawler will email you again when it&#39;s time to sell.</p>'
        )
    else:
        pnl = signal.get("pnl")
        ret = signal.get("return_pct")
        pnl_line = (f' Your simulated trade result: <strong style="color:{"#00E676" if (pnl or 0)>=0 else "#FF3366"}">'
                    f'${pnl}</strong>.' if pnl is not None else "")
        tiers_html = ""
        if ret is not None:
            rows = ""
            for c in (20, 50, 100):
                res = c * ret / 100.0
                col = "#00E676" if res >= 0 else "#FF3366"
                sign = "+" if res >= 0 else "-"
                rows += (
                    f'<tr>'
                    f'<td style="padding:7px 10px;border-top:1px solid #1F1F24;font-size:14px">${c}</td>'
                    f'<td style="padding:7px 10px;border-top:1px solid #1F1F24;font-size:14px;color:{col};text-align:right">'
                    f'{sign}${abs(res):,.2f}</td>'
                    f'</tr>'
                )
            tiers_html = (
                f'<p style="font-size:16px;margin:16px 0 6px"><strong>This trade&#39;s result by stake size:</strong></p>'
                f'<table role="presentation" width="100%" style="border-collapse:collapse;background:#121215;border-radius:6px">'
                f'<tr>'
                f'<td style="padding:7px 10px;font-size:11px;color:#8A8A93;text-transform:uppercase;letter-spacing:1px">If you put in</td>'
                f'<td style="padding:7px 10px;font-size:11px;color:#8A8A93;text-transform:uppercase;letter-spacing:1px;text-align:right">Result (~{ret:.2f}%)</td>'
                f'</tr>{rows}</table>'
                f'<p style="font-size:12px;color:#8A8A93;margin-top:6px">Before trading fees. Based on the entry and exit prices for this signal.</p>'
            )
        plan_html = (
            f'<p style="font-size:16px;margin:14px 0 4px"><strong>What to do now:</strong></p>'
            f'<p style="font-size:15px;margin:2px 0">Time to <strong>sell</strong> your {escape(coin)} position, '
            f'around <strong>${price:,.4f}</strong>.{pnl_line}</p>'
            f'{tiers_html}'
        )

    return (
        f'<table role="presentation" width="100%" style="background:#0A0A0C;padding:24px">'
        f'<tr><td style="font-family:Arial,sans-serif;color:#EDEDED">'
        f'<p style="font-size:12px;letter-spacing:2px;color:#8A8A93;text-transform:uppercase">'
        f'{escape(EMAIL_FROM_NAME)} &middot; Trade Alert</p>'
        f'<h1 style="color:{color};margin:8px 0;font-size:30px">{escape(side)} {escape(coin)}</h1>'
        f'<p style="font-size:14px;color:#8A8A93;margin:0">From bot: {escape(strategy_name)} &middot; {escape(signal.get("timeframe",""))} chart</p>'
        f'{plan_html}'
        f'<div style="background:#FFE600;color:#000;padding:10px;font-size:11px;'
        f'font-weight:bold;letter-spacing:1px;margin-top:18px;text-align:center">'
        f'NOT FINANCIAL ADVICE. THIS IS A SUGGESTION ONLY &mdash; THE BOT PLACES NO ORDERS. TRADE AT YOUR OWN RISK.</div>'
        f'<p style="font-size:11px;color:#565660;margin-top:16px">Sent by {escape(EMAIL_FROM_NAME)}. '
        f'We never ask for your password or payment details by email.</p>'
        f'</td></tr></table>'
    )


async def get_settings() -> dict:
    s = await db.settings.find_one({"id": "global"}, {"_id": 0})
    if not s:
        s = {
            "id": "global",
            "alert_emails": [],
            "email_enabled": True,
            "sms_enabled": False,
            "max_alerts_per_hour": 10,
            "max_emails_per_day": 50,
            "min_minutes_between_same_strategy": 15,
            "trade_timeframe": "15m",
            "max_hold_hours": 5,
            "auto_approve": False,
            "auto_approve_min_winprob": 0.85,
            "trailing_enabled": True,
        }
        await db.settings.insert_one(dict(s))
        return s
    # migrate single alert_email -> alert_emails list
    if "alert_emails" not in s:
        legacy = [s["alert_email"]] if s.get("alert_email") else []
        s["alert_emails"] = legacy
        await db.settings.update_one({"id": "global"}, {"$set": {"alert_emails": legacy}})
    return s


def _recipients(settings: dict) -> list:
    emails = settings.get("alert_emails") or []
    if not emails and settings.get("alert_email"):
        emails = [settings["alert_email"]]
    return [e.strip() for e in emails if e and e.strip()]


def _proposal_html(prop: dict) -> str:
    side = prop.get("side")
    is_buy = side == "BUY"
    color = "#00E676" if is_buy else "#FF3366"
    sym = escape(str(prop.get("mt5_symbol", "")))
    entry = prop.get("entry_ref", 0)
    sl = prop.get("stop_loss", 0)
    tp = prop.get("take_profit", 0)
    lot = prop.get("lot", 0)
    risk = prop.get("est_risk_usd")
    conf = prop.get("confidence", 0)
    win = prop.get("win_prob")
    er = prop.get("expected_r")

    if is_buy:
        headline = (f"The crawler predicts <strong style=\"color:#00E676\">{sym} will rise</strong> "
                    f"and found a <strong>BUY (long)</strong> trade to profit from the move up.")
    else:
        headline = (f"The crawler predicts <strong style=\"color:#FF3366\">{sym} will fall (go red)</strong> "
                    f"and found a <strong>SELL (short)</strong> trade to profit from the move down.")

    win_line = (f'<p style="font-size:15px;margin:2px 0">Simulation: '
                f'<strong>{int(win*100)}% win odds</strong>, '
                f'<strong>{er:+.2f}R</strong> expected.</p>') if win is not None and er is not None else ""
    hold = prop.get("hold_label")
    hold_line = (f'<p style="font-size:15px;margin:2px 0">Typical hold: '
                 f'<strong>{escape(str(hold))}</strong> (day-trade, 30min-5h).</p>') if hold else ""
    risk_line = (f'<p style="font-size:15px;margin:2px 0">Sized to risk about '
                 f'<strong>${risk:,.2f}</strong> if the stop-loss hits.</p>') if risk is not None else ""

    return (
        f'<table role="presentation" width="100%" style="background:#0A0A0C;padding:24px">'
        f'<tr><td style="font-family:Arial,sans-serif;color:#EDEDED">'
        f'<p style="font-size:12px;letter-spacing:2px;color:#8A8A93;text-transform:uppercase">'
        f'{escape(EMAIL_FROM_NAME)} &middot; Demo Trade Idea</p>'
        f'<h1 style="color:{color};margin:8px 0;font-size:30px">{escape(side)} {sym}</h1>'
        f'<p style="font-size:15px;margin:6px 0">{headline}</p>'
        f'<p style="font-size:16px;margin:14px 0 4px"><strong>The plan:</strong></p>'
        f'<p style="font-size:15px;margin:2px 0">Entry around <strong>{entry:,.4f}</strong> &middot; '
        f'{lot} lots.</p>'
        f'<p style="font-size:15px;margin:2px 0">Take-profit: '
        f'<strong style="color:#00E676">{tp:,.4f}</strong> &middot; '
        f'Stop-loss: <strong style="color:#FF3366">{sl:,.4f}</strong>.</p>'
        f'<p style="font-size:15px;margin:2px 0">Confidence <strong>{conf}%</strong>.</p>'
        f'{win_line}{hold_line}{risk_line}'
        f'<div style="background:#00E5FF;color:#000;padding:12px;font-size:14px;'
        f'font-weight:bold;margin-top:16px;text-align:center;border-radius:6px">'
        f'Open TradeCrawler to APPROVE or REJECT this trade</div>'
        f'<div style="background:#FFE600;color:#000;padding:10px;font-size:11px;'
        f'font-weight:bold;letter-spacing:1px;margin-top:14px;text-align:center">'
        f'DEMO ACCOUNT &middot; VIRTUAL FUNDS &middot; NOT FINANCIAL ADVICE &middot; NO ORDER IS PLACED WITHOUT YOUR APPROVAL</div>'
        f'<p style="font-size:11px;color:#565660;margin-top:16px">Sent by {escape(EMAIL_FROM_NAME)}. '
        f'We never ask for your password or payment details by email.</p>'
        f'</td></tr></table>'
    )


async def email_proposal(prop: dict) -> dict:
    """Email the user when the bot finds a new BUY or SELL trade idea (demo).
    Respects recipients, a per-symbol+side cooldown, and the daily cap."""
    settings = await get_settings()
    recipients = _recipients(settings)
    if not settings.get("email_enabled") or not recipients:
        return {"sent": False, "reason": "email disabled or no recipients"}

    now = datetime.now(timezone.utc)
    key = f"proposal:{prop.get('mt5_symbol')}:{prop.get('side')}"
    cooldown = settings.get("min_minutes_between_same_strategy", 15)
    cutoff = (now - timedelta(minutes=max(cooldown, 30))).isoformat()
    if await db.alerts.count_documents({"idempotency_key": {"$regex": f"^{key}"}, "sent_at": {"$gte": cutoff}}):
        return {"sent": False, "reason": "cooldown"}

    day_cutoff = (now - timedelta(days=1)).isoformat()
    daily = await db.alerts.count_documents({"channel": "email", "sent_at": {"$gte": day_cutoff}})
    if daily >= settings.get("max_emails_per_day", 50):
        return {"sent": False, "reason": "daily cap"}

    try:
        subject = f"[{prop['side']}] {prop['mt5_symbol']} — TradeCrawler found a trade (approve in app)"
        email_id = await send_email(to=recipients, subject=subject, html=_proposal_html(prop))
        await db.alerts.insert_one({
            "idempotency_key": f"{key}:{prop.get('id')}",
            "proposal_id": prop.get("id"),
            "channel": "email",
            "kind": "proposal",
            "symbol": prop.get("mt5_symbol"),
            "side": prop.get("side"),
            "to": recipients,
            "email_id": email_id,
            "sent_at": now.isoformat(),
        })
        return {"sent": True, "id": email_id}
    except Exception as e:  # noqa
        logger.error(f"Proposal email failed: {e}")
        return {"sent": False, "reason": str(e)}



async def maybe_send_alert(signal: dict, strategy: dict) -> dict:
    """Apply gating, dedup and rate-limits. Returns dict describing what happened."""
    result = {"emailed": False, "reason": None}
    settings = await get_settings()
    recipients = _recipients(settings)

    if not strategy.get("gate_passed"):
        result["reason"] = "edge not proven yet (bot still testing)"
        return result
    if not settings.get("email_enabled") or not recipients:
        result["reason"] = "email disabled or no recipients set"
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
        email_id = await send_email(to=recipients, subject=subject, html=html)
        await db.alerts.insert_one({
            "idempotency_key": signal["sig_key"],
            "strategy_id": strategy["id"],
            "signal_id": signal.get("id"),
            "channel": "email",
            "to": recipients,
            "email_id": email_id,
            "sent_at": now.isoformat(),
        })
        result["emailed"] = True
        result["reason"] = "sent"
    except Exception as e:  # noqa
        logger.error(f"Alert email failed: {e}")
        result["reason"] = f"send error: {e}"
    return result
