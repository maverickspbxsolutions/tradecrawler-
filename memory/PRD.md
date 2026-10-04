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

## Iteration — Richer signal emails (2026-06)
- BUY alert emails now include: explicit BUY/SELL action, **Take-profit (TP)** and **Stop-loss (SL)**
  price levels (from backtest avg win/loss %), **how long to stay in the trade** (avg hold time,
  humanized), and an **estimated profit/risk table for $20 / $50 / $100** stakes.
- SELL alert emails show the realized result by stake size ($20/$50/$100) from the signal's return %.
- Dashboard UNCHANGED — still assumes $20 (SignalFeed PlanLine reads only capital/entry/target/est_profit).
  Multi-stake tiers are computed inside the email builder only; `hold_label` added to plan is ignored by UI.
- Backend: backtester.py now returns `avg_hold_ms`; paper_engine `_trade_plan` adds `hold_label`
  (`_humanize_duration`); alerts.py `_signal_html` rebuilt (passes the phishing-safety scan — tables only,
  no forms/inputs, no external links). All existing strategy backtests re-run to populate avg_hold_ms.

## Iteration — Real data via Yahoo + theme polish (2026-06)
- DATA FIX: Deriv WS is Cloudflare-blocked (HTTP 520) from Emergent egress. Deriv instruments with a
  real-world equivalent now pull REAL data from **Yahoo Finance** (reachable, no key): Gold (GC=F),
  metals, Deriv-crypto (BTC-USD…), forex (EURUSD=X…), stock indices (^GSPC…). See `DERIV_TO_YAHOO` map +
  `_fetch_yahoo()` in market_data.py; `get_candles`/`get_live` route these to Yahoo, 4h is resampled from 60m.
  Only Deriv-proprietary SYNTHETIC/volatility indices (R_*, 1HZ*, BOOM*, CRASH*, Step, Jump, Bull/Bear)
  have no external source → HTTP 200 `{available:false, reason}` with a clear "Deriv-only" message.
- THEME FIX: added `lib/useChartTheme.js` (reads CSS vars per resolved theme). Replaced hardcoded dark
  hex (#1F1F24/#8A8A93/#121215/#1A1A1F/#00E5FF/#565660) in Candlestick, PriceChart, MarketOutlook,
  BacktestPanel, PaperPanel, EvolutionPanel, SignalFeed with theme-aware colors. Fixed DisclaimerBanner
  (bg-warn wasn't applying → now explicit `hsl(var(--warn))`). Light + dark both verified cohesive.
- Tested: iteration_8.json — backend 24/24 pytest; Yahoo-backed candles/outlook/live/backtest verified;
  synthetic graceful message; theme toggle both ways across all panels.

## Known Caveats (Deriv App ID)
- DERIV_APP_ID stays numeric "1089" (public market-data WS). User's OAuth App ID 34zIy2UfIdAzHQNsLhUod
  saved as DERIV_OAUTH_APP_ID for future login features (not needed for market data).

## Iteration — Smarter Mini Simulations (2026-06)
- `/api/strategies/{sid}/simulate` upgraded for accuracy:
  - **Block bootstrap** (streak-aware): samples contiguous runs of past trades so win/loss streaks,
    drawdown and tail risk are realistic (vs naive IID shuffle).
  - **Current-market tuning**: fetches live candles once, scales per-trade magnitude by current vs
    reference volatility (clamped 0.6–1.6) and tilts drift by the live Market Outlook direction×confidence
    (±0.4%/trade max). Falls back to untuned history if data unavailable (synthetic indices).
  - New stats: `median_max_drawdown_pct` (worst dip), `prob_lose_half`, `expected_duration`
    (avg_hold_ms × trades, humanized — Gold/indices ≈ weeks, crypto 5m ≈ hours). Returns a `tuning` block
    (direction, confidence, vol_ratio, regime).
- Frontend MiniSimulations.jsx: added Worst-dip + Chance-of-losing-half chips, horizon line, and a
  "Tuned to current market" note. Dashboard stays at $20. Framing remains "rough preview, not advice".
- Verified via curl (stats + tuning present) and screenshot (renders in dark/light).

## Iteration — Deriv MT5 live data + MetaApi trading bridge (2026-06)
- Connected a **Deriv MT5 demo** account (login 32430043, server Deriv-Demo, $10k demo) via **MetaApi**
  (metaapi.cloud) — the cloud bridge needed because MT5's native lib is Windows-only. MetaApi is reachable
  from our egress (unlike Deriv's blocked WS). Secrets in backend/.env: METAAPI_TOKEN, MT5_LOGIN,
  MT5_PASSWORD, MT5_SERVER, MT5_ACCOUNT_ID, METAAPI_REGION. SDK: metaapi-cloud-sdk.
- `backend/mt5_trader.py`: long-lived RPC connection (warmed at startup), `candles()` / `live_price()` /
  `get_universe()` (727 real Deriv symbols) + trade primitives `market_order()` / `close_position()` /
  `positions()` / `account_info()`. `mt5_symbol()` maps our codes (frx*, cry*, *USDT, R_*, BOOM*, OTC_*)
  to MT5 names.
- market_data.py now uses **Deriv MT5 as the PRIMARY data source** for all resolvable symbols
  (get_candles & get_live route to MT5 first, source "deriv-mt5"), falling back to Binance/Yahoo.
  This unblocks synthetic/volatility indices (Volatility 75/100, Boom/Crash) which only exist on Deriv.
  `/api/market/symbols` now serves the live 727-symbol MT5 universe grouped (Metals, Crypto, Synthetic &
  Volatility, Stock Indices, Forex, Stocks, Energies), default BTCUSD.
- Chart matched to MT5: real Deriv OHLC, green-up/red-down, right-axis price labels, MT5-style colored
  current-price tag, crosshair with OHLC+time readout, 3s live updates.
- Verified: XAUUSD/Volatility 75/BTCUSD candles+live+outlook via MT5; Gold & "100 Index" backtests run
  on real MT5 data; chart screenshot confirms MT5 look.

## Demo Trade Execution Engine (BUILT — 2026-10-04, agent-tested)
Approve-each-trade engine on the connected Deriv MT5 DEMO account via MetaApi.
- Files: `backend/risk_config.py`, `backend/trade_engine.py`, `backend/mt5_trader.py` (market_order
  now takes SL/TP + clientId; added symbol_spec), routes under `/api/trades/*`,
  `frontend/src/components/TradePanel.jsx` (wired into Dashboard right column).
- Flow: Scan Gold/BTC -> engine builds a PROPOSAL -> Approval Queue (approve/reject) -> on approve the
  server runs risk checks and places a market order with SL/TP. Nothing auto-executes.
- Smart logic (anti "willy-nilly"): only proposes when outlook confidence >= 58% AND a forward
  Monte-Carlo simulation (drift tilted by the live outlook, 600 paths, 24-bar horizon) shows
  win_prob >= 0.45 and expected >= 0.05R. SL/TP from ATR (1.5R reward:risk), respects broker stopsLevel.
- Risk-based sizing: lot computed so a stop-out loses ≈ RISK_PER_TRADE_USD ($12.50 = limit/(maxpos+1))
  using value_per_unit = contractSize (Deriv CFDs expose no tickValue; USD profit ccy). Clamped to the
  symbol's volume step/min/max and MAX_LOT (10). Verified: BTC proposal sized to 0.05 lots / ~$11.59 risk.
- Hard caps (risk_config.py): demo-env lock (refuses non-demo account), Gold/BTC allowlist, MAX_LOT 10,
  MAX_OPEN_POSITIONS 3 (concurrent cap verified: 4th blocked), $50 daily loss limit.
- KILL SWITCH IS DEMO-DISABLED per user: ENFORCE_KILL_SWITCH = (TRADE_ENV != "demo") -> False on demo.
  Daily P&L still tracked/shown (tracking-only). Re-arms automatically when TRADE_ENV flips to live.
- clientId must be `strategyId_positionId_orderId` format (underscores), comment+clientId <= 30 chars.
  Engine uses `TC_<SYM6>_<uuid8>` with comment "TC".
- Endpoints: GET /trades/account, /trades/pnl, /trades/positions, /trades/log;
  POST /trades/propose, /trades/proposals/{id}/approve, /trades/proposals/{id}/reject,
  /trades/positions/{id}/close. All agent-tested via curl (place+SL/TP, reject, concurrent cap, close).
- requirements.txt updated (pip freeze) so metaapi_cloud_sdk + pandas/numpy persist for deploy.
- NOT user-confirmed in production yet. Live trading stays OFF until explicit go-live.

## Dynamic universe + auto-hunt (BUILT — 2026-10-04, agent-tested)
- Tradeable universe is now DYNAMIC: Gold/BTC defaults PLUS the symbol of every ENABLED strategy
  (`trade_engine._scan_targets` / `allowed_symbols` / `active_instruments`). Turn on a strategy and its
  instrument becomes tradeable automatically (e.g. "Volatility 100 Index").
- `mt5_trader.mt5_symbol` now passes through already-valid MT5 symbol names (strategies may store
  "Volatility 100 Index" directly, not "R_100"). Uses `_MT5_SYMBOLS = set(SYMBOL_MAP.values())`.
- Background auto-scanner: `scheduler._scan_loop` every 90s calls `trade_engine.auto_scan()`, which scans
  each active instrument and auto-creates a PROPOSAL when the confidence+simulation gates pass. Deduped:
  at most one pending proposal per symbol, skips symbols with an open bot trade. DEMO-ONLY (refuses if
  account isn't demo). It only PROPOSES — never auto-approves (approve stays a manual user action).
- Frontend: scan buttons are rendered from GET /api/trades/active-symbols (dynamic). Verified live: queue
  auto-filled SELL XAUUSD + BUY BTCUSD; Volatility 100 scanned and correctly declined at 31% confidence.
- Gold min-lot note: XAUUSD contractSize=100, minVolume=0.01 -> smallest gold trade risks ~$34 (can't go
  lower). Crypto/synthetics (contractSize 1) size finely to the ~$12.50 target.

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
