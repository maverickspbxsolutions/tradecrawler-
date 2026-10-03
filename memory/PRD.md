# Crypto Signal Terminal — PRD

## Original Problem Statement
Web-based crypto signaling bot (alerts-only, no order execution). Rule-based signal engine
(RSI, MA crossover, MACD, volume), backtesting with fees+slippage vs buy-and-hold, live
paper-trading forward simulation, email/SMS alerts with de-dup + rate-limiting, dark
trading-terminal dashboard, and strict edge-case handling (closed-candle only / no repaint,
no lookahead, idempotent alerts). Plus an always-on Adaptive Evolution Engine that
continuously optimizes strategy parameters via walk-forward genetic search with
out-of-sample validation and market-regime detection.

## User Choices
- Market data: Binance.US primary (Binance.com geo-blocked 451) + Coinbase fallback.
- Email: user's OWN Resend account (direct, independent of Emergent). SMS deferred (Twilio-ready).
- Auth: single shared JWT account — username `xambiep90`.
- Timeframes: 1m–1d selectable. ALL 202 Binance.US USDT pairs selectable, BTCUSDT default.
- UI: dark terminal, candlestick charts (green up / red down), non-negotiable disclaimer banner.
- Evolution: auto-apply only if candidate beats current config out-of-sample; daily + manual; risk-adjusted fitness.

## Architecture
- Backend: FastAPI modular — db, auth, market_data, indicators, strategy_engine, backtester,
  paper_engine, alerts (direct Resend), evolution, scheduler (asyncio poller 30s + daily evolve), routes.
- DB: MongoDB (uuid string ids, _id projected out). Collections: users, strategies, backtests,
  paper_states, paper_trades, signals, alerts, settings, evolutions, engine_status.
  Unique indexes: signals.sig_key, alerts.idempotency_key (DB-level dedup).
- Frontend: React + Tailwind + shadcn, react-query, recharts (equity/fitness), custom SVG
  candlesticks. Dark-only terminal theme (Azeret Mono + IBM Plex Sans).

## Implemented (2026-10-03)
- JWT bearer auth, single seeded shared account; protected /api routes.
- Market data w/ retries, fallback, in-memory TTL cache, data-health/staleness; 202-symbol list.
- Composable strategies (entry/exit rules, AND/OR), CRUD + enable/live toggles.
- Backtester: no lookahead (fill next open), fees+slippage, expectancy, R:R, max drawdown,
  win rate (flagged secondary), equity curve vs buy-and-hold, gate (beats bench + expectancy>0).
- Live gate enforcement: cannot go live unless gate passed.
- Paper-trading engine (long-only state machine), per-strategy P&L, equity curve, trades.
- Signal feed (closed-candle only, idempotent sig_key); historical backfill for populated feed.
- Alerts via direct Resend: de-dup (idempotency), per-strategy cooldown, hourly rate limit,
  daily email cap; gated to live+gate-passed strategies; "not financial advice" footer.
- Adaptive Evolution Engine: GA (pop 12, 8 gens) walk-forward (in-sample optimize, OOS validate),
  risk-adjusted fitness, regime detection (trend/volatility/strength), auto-apply iff beats current OOS.
- Dashboard: candlestick chart + timeframe selector, stats, signal feed, strategy cards,
  backtest panel, paper panel, evolution panel, settings, searchable all-crypto combobox.

## Known Caveats
- Resend sender defaults to `onboarding@resend.dev` (RESEND_FROM_EMAIL). To deliver to any
  inbox, the user must verify a domain in Resend and set RESEND_FROM_EMAIL to that domain address.
- SMS (Twilio) intentionally deferred; settings/architecture ready (sms_enabled flag).

## Backlog / Next
- P1: Twilio SMS with daily caps + email-only fallback.
- P1: Friend accounts (multi-user) if needed.
- P2: More indicators (Bollinger, ATR stops), multi-timeframe confluence.
- P2: Persisted equity snapshots for intraday paper curve granularity.
- P2: SymbolCombobox virtualization if symbol count grows.
