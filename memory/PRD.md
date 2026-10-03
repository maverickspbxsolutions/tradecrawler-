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

## Iterations (post-MVP)
- TradeCrawler rebrand; beginner plain-English UI + tooltips + "How it works" card.
- Direct Resend (user's own key, independent of Emergent); multi-recipient alerts; $20 trade-plan emails.
- Autonomous 24/7: alerts auto-fire the moment a strategy proves its edge (no manual go-live); 30s poll.
- All 202 Binance.US USDT pairs selectable (searchable combobox), BTC default.
- Light/Dark theme toggle (next-themes).
- Mini Simulations panel (Monte-Carlo of $20 over next 20 trades) below Trade Alerts.
- Deployment-readiness scan passed (fixed N+1 in /api/strategies).

## Iteration — Live data + Market Outlook (2026-06, this fork)
- Real-time charts: PriceChart now polls `/api/market/live` every 5s and overlays the in-progress
  (forming) candle on top of closed candles; header shows live price + "Live · Xs ago" pulsing
  indicator. Closed candles still drive signals (no repaint/lookahead risk).
  Backend `get_live()` (market_data.py) = current price + forming candle, 3s TTL cache.
- Market Outlook prediction: new `backend/outlook.py` `compute_outlook()` + `GET /api/market/outlook`.
  Multi-indicator confluence (trend structure EMA20/50, trend slope, RSI momentum, MACD momentum,
  recent drift, volatility regime) → direction Up/Down/Neutral + confidence% + bias score + plain-English
  reasons. Frontend `MarketOutlook.jsx` panel below the chart; shares timeframe with the chart
  (tf lifted into Dashboard). Framed as probabilistic read, NOT a guarantee / not advice.
- Deriv dropdown ALWAYS populated: `DERIV_FALLBACK_SYMBOLS` static list (Gold, metals, Deriv crypto,
  Volatility 10/25/50/75/100 + (1s), Boom/Crash, Step, Jump, Bear/Bull, forex majors, stock indices).
  `/api/market/symbols` returns ~254 options (Binance crypto + 52 Deriv), Gold/BTC/Volatility ranked first.
- Graceful Deriv degradation: Deriv WS is UNREACHABLE from Emergent egress (Cloudflare HTTP 520).
  `/api/market/candles`, `/api/market/live`, `/api/market/outlook` now return **HTTP 200 with
  `{available:false, reason}`** (NOT 502) when the Deriv source is down, so Cloudflare can't rewrite
  the body into its own error page. UI shows friendly "Live data for this market is temporarily
  unavailable." (chart + outlook) and header reads "Unavailable". `DerivUnavailable` exception +
  60s negative-cache block so Deriv requests don't each eat the ~5s WS open-timeout (repeats ~0.15s).
- Tested: iteration_7.json — backend 12/12 pytest; all frontend flows + graceful degradation verified.

## Known Caveats
- Deriv market data (Gold, Volatility Indices, forex, stock indices) is UNREACHABLE from the
  Emergent builder/preview egress (Cloudflare HTTP 520). Deriv instruments still appear in the picker
  (static fallback) and the app degrades gracefully. Deriv candles/outlook should start working from
  a deployed runtime with different network egress — validate `active_symbols` + candles for
  frxXAUUSD, cryBTCUSD and R_100 after deploy. A numeric Deriv app_id (not a pat_ token) may be needed.
- Deriv App ID clarification: the user's Deriv app is an OAuth app, App ID `34zIy2UfIdAzHQNsLhUod`
  (saved as DERIV_OAUTH_APP_ID, redirect https://tradecrawler.live/). Per Deriv docs, OAuth App IDs are
  for account LOGIN flows; market data is a PUBLIC no-auth WebSocket that uses the legacy NUMERIC app_id.
  So DERIV_APP_ID stays "1089" (public numeric) for charts/signals; the OAuth App ID is only needed if
  "log in with Deriv"/account features are added later (out of scope — TradeCrawler is alerts-only).
- For SPOT crypto (BTC/ETH/etc.), Binance.US is the more accurate real-market source; Deriv crypto is
  a CFD/synthetic feed. Crypto live price + Market Outlook are fully working now.
- Resend sender defaults to `onboarding@resend.dev` (RESEND_FROM_EMAIL). To deliver to any
  inbox, the user must verify a domain in Resend and set RESEND_FROM_EMAIL to that domain address.
- SMS (Twilio) intentionally deferred; settings/architecture ready (sms_enabled flag).

## Backlog / Next
- P1: Twilio SMS with daily caps + email-only fallback.
- P1: Friend accounts (multi-user) if needed.
- P2: More indicators (Bollinger, ATR stops), multi-timeframe confluence.
- P2: Persisted equity snapshots for intraday paper curve granularity.
- P2: SymbolCombobox virtualization if symbol count grows.
