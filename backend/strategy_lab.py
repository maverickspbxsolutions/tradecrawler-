"""Strategy Lab: turn a trader's free-form description or an uploaded file
(PDF/CSV/TXT) into the engine's structured, rule-based Strategy schema using
Claude. NO user code is ever executed — only structured rules + learned priors.

Also maintains a lightweight global 'platform edge' that blends the biases of
contributed strategies so the bot gets sharper for everyone, including users who
never uploaded anything."""
import os
import io
import json
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv
from emergentintegrations.llm.chat import LlmChat, UserMessage

from db import db

load_dotenv()
logger = logging.getLogger(__name__)

LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")
MODEL = ("anthropic", "claude-sonnet-5-5")

_SCHEMA_GUIDE = """You convert a retail trader's strategy into a STRICT JSON object the trading
engine can run. The engine ONLY supports these rule building blocks — map everything to them:

- {"indicator":"rsi","params":{"period":14,"value":30,"op":"below"}}            # op: "below"|"above"
- {"indicator":"ma_cross","params":{"fast":9,"slow":21,"ma_type":"ema","direction":"bull"}}  # ma_type: "ema"|"sma"; direction: "bull"|"bear"
- {"indicator":"macd","params":{"fast":12,"slow":26,"signal":9,"direction":"bull"}}          # direction: "bull"|"bear"
- {"indicator":"volume","params":{"period":20,"multiplier":1.5}}

Output ONLY a JSON object (no prose, no code fences) with EXACTLY these keys:
{
  "name": "short strategy name",
  "symbol": "a ticker if the trader named one, else \\"XAUUSD\\"",
  "timeframe": "one of 5m,15m,30m,1h,4h,1d (pick what best fits; default 15m)",
  "logic": "AND" or "OR",
  "entry_rules": [ <rule objects from the list above> ],
  "exit_rules": [ <rule objects; may be empty> ],
  "style_notes": "1-3 plain-English sentences capturing HOW this person trades",
  "bias": {
     "direction": "long" | "short" | "both",
     "regime": "trend" | "range" | "any",
     "entry_style": "pullback" | "breakout" | "reversal" | "any",
     "risk_appetite": "low" | "medium" | "high"
  },
  "confidence": <0-100 integer: how well the description mapped to concrete rules>
}
If the description is vague, infer sensible defaults and lower the confidence. Never invent
indicators outside the four listed. Keep entry_rules to 1-4 items."""

_ALLOWED = {"rsi", "ma_cross", "macd", "volume"}
_TF = {"5m", "15m", "30m", "1h", "4h", "1d"}


def _coerce(parsed: dict) -> dict:
    """Keep only engine-valid rules/fields; fill safe defaults."""
    def clean_rules(rules):
        out = []
        for r in rules or []:
            ind = (r or {}).get("indicator")
            if ind in _ALLOWED:
                out.append({"indicator": ind, "params": (r.get("params") or {})})
        return out[:4]

    bias = parsed.get("bias") or {}
    tf = parsed.get("timeframe") if parsed.get("timeframe") in _TF else "15m"
    return {
        "name": str(parsed.get("name") or "My Strategy")[:60],
        "symbol": str(parsed.get("symbol") or "XAUUSD")[:24],
        "timeframe": tf,
        "logic": "OR" if str(parsed.get("logic")).upper() == "OR" else "AND",
        "entry_rules": clean_rules(parsed.get("entry_rules")),
        "exit_rules": clean_rules(parsed.get("exit_rules")),
        "style_notes": str(parsed.get("style_notes") or "")[:600],
        "bias": {
            "direction": bias.get("direction") if bias.get("direction") in ("long", "short", "both") else "both",
            "regime": bias.get("regime") if bias.get("regime") in ("trend", "range", "any") else "any",
            "entry_style": bias.get("entry_style") if bias.get("entry_style") in ("pullback", "breakout", "reversal", "any") else "any",
            "risk_appetite": bias.get("risk_appetite") if bias.get("risk_appetite") in ("low", "medium", "high") else "medium",
        },
        "confidence": int(parsed.get("confidence") or 50),
    }


async def parse_strategy(text: str) -> dict:
    """Send the trader's text to Claude and return a coerced, engine-ready strategy."""
    if not LLM_KEY:
        raise RuntimeError("AI key not configured")
    text = (text or "").strip()
    if not text:
        raise ValueError("Nothing to interpret — add a description or file.")
    chat = LlmChat(api_key=LLM_KEY, session_id=f"stratlab-{datetime.now().timestamp()}",
                   system_message=_SCHEMA_GUIDE).with_model(*MODEL)
    reply = await chat.send_message(UserMessage(text=f"Trader's strategy:\n\n{text[:6000]}"))
    raw = reply.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].replace("json", "", 1).strip() if "```" in raw else raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        parsed = json.loads(raw[start:end + 1])
    result = _coerce(parsed)
    if not result["entry_rules"]:
        # guarantee at least one runnable rule so the strategy isn't a no-op
        result["entry_rules"] = [{"indicator": "ma_cross",
                                  "params": {"fast": 9, "slow": 21, "ma_type": "ema", "direction": "bull"}}]
        result["confidence"] = min(result["confidence"], 40)
    return result


def extract_text(filename: str, content: bytes) -> str:
    """Pull text from an uploaded PDF / CSV / TXT (server-side, no code exec)."""
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(content))
        return "\n".join((p.extract_text() or "") for p in reader.pages)[:12000]
    if name.endswith(".csv"):
        import pandas as pd
        df = pd.read_csv(io.BytesIO(content))
        head = df.head(40).to_csv(index=False)
        summary = (f"CSV of {len(df)} rows, columns: {list(df.columns)}.\n"
                   f"First rows:\n{head}\n"
                   "If these look like past trades, infer the trader's style, which setups "
                   "won most, preferred direction/regime, and risk appetite.")
        return summary[:12000]
    return content.decode("utf-8", errors="ignore")[:12000]


async def recompute_platform_edge() -> dict:
    """Aggregate the biases of ALL contributed strategies into a global edge doc
    the decision engine can lean on (benefits every user, uploader or not)."""
    strats = await db.strategies.find(
        {"ai_bias": {"$exists": True}}, {"_id": 0, "ai_bias": 1, "owner_id": 1}).to_list(2000)
    counts = {"direction": {}, "regime": {}, "entry_style": {}, "risk_appetite": {}}
    contributors = set()
    for s in strats:
        b = s.get("ai_bias") or {}
        contributors.add(s.get("owner_id"))
        for k in counts:
            v = b.get(k)
            if v and v != "any" and v != "both":
                counts[k][v] = counts[k].get(v, 0) + 1

    def top(d):
        return max(d.items(), key=lambda x: x[1])[0] if d else None

    edge = {
        "_id": "platform_edge",
        "contributed_strategies": len(strats),
        "contributors": len([c for c in contributors if c]),
        "preferred_direction": top(counts["direction"]),
        "preferred_regime": top(counts["regime"]),
        "preferred_entry_style": top(counts["entry_style"]),
        "risk_appetite": top(counts["risk_appetite"]),
        "distribution": counts,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.bot_meta.replace_one({"_id": "platform_edge"}, edge, upsert=True)
    return {k: v for k, v in edge.items() if k != "_id"}


async def get_platform_edge() -> dict:
    doc = await db.bot_meta.find_one({"_id": "platform_edge"})
    if not doc:
        return await recompute_platform_edge()
    doc.pop("_id", None)
    return doc
