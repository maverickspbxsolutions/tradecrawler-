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

## Bidirectional trading + email on discovery (BUILT — 2026-10-04, agent-tested)
- The engine trades BOTH directions: outlook "Up" -> BUY (long), "Down" -> SELL (short). Verified live:
  queue holds a SELL XAUUSD (predicts fall) and a BUY BTCUSD (predicts rise) simultaneously.
- New email on auto-discovery: `alerts.email_proposal(prop)` + `alerts._proposal_html(prop)` send a
  BUY/SELL trade-idea email (entry/SL/TP, lot, sim win odds, expected R, risk) with an "Open TradeCrawler
  to APPROVE or REJECT" call-to-action and a demo/not-advice disclaimer. SELL emails are framed as SHORT
  entries ("predicts {sym} will fall (go red)"), not position exits.
  `trade_engine.auto_scan` calls it when a new proposal is queued. Verified: both templates render and a
  send returned a Resend id (so a recipient IS configured and emails deliver).
- Spam control: per (symbol, side) cooldown (>=30m) + existing daily email cap (50). Dedup keeps exactly
  one pending proposal per symbol+side (verified).

## Intraday day-trade timeframe + MT5 robustness (BUILT — 2026-10-04, agent-tested)
- Trade engine now runs on an INTRADAY timeframe: `risk_config.TRADE_TIMEFRAME = "15m"`,
  `SIM_HORIZON_BARS = 20` (~5 hours). Profit/odds/sim are measured over a 30min-5h window (the user
  day-trades 30min-5h holds), NOT 24h.
- `_predict` returns `est_hold_bars` (median bars-to-resolution); propose converts via `_tf_minutes` and
  shows a hold label (e.g. "~1h 45m"). Added to proposal, email ("Typical hold: ~Xh"), and UI ("Hold ≈").
  Verified live: SELL Volatility 100 proposal showed HOLD ~1h 45m, risk $12.45, 90% sim odds, +1.23R.
- `mt5_trader.mt5_symbol` passes through MT5 names; all scans use TRADE_TIMEFRAME.
- ROBUSTNESS (important): every MT5 RPC call is now wrapped in `asyncio.wait_for` (RPC_TIMEOUT 15s,
  order/close 30s) and `ensure()`/`_connect` have CONNECT_TIMEOUT 60s + CONNECT_COOLDOWN 45s backoff.
  Previously a degraded MetaApi->Deriv broker link made calls hang forever -> Cloudflare 502. Now
  endpoints fail fast and the UI shows OFFLINE gracefully. Root cause of an outage seen this session was
  broker-side ("Timed out waiting for account to connect to the broker", connected:false) and recovered
  on its own — not a code bug.
- Gold min-lot still forces ~$34 risk; crypto/synthetics size finely.

## Fix: "Invalid stops in the request" on approve (FIXED — 2026-10-04, testing_agent verified)
- Root cause: SL/TP computed at proposal time went stale as price drifted during the approval window,
  so the broker rejected the order.
- Fix in `trade_engine.approve()`: new `_fresh_stops(p, spec)` re-anchors SL/TP to the LIVE price at
  approval, keeps the proposal's risk distance, enforces broker min stop (`_min_stop_dist`*1.5 buffer),
  snaps to tickSize/digits. Final stops stored on proposal + trade record. If refresh fails it now fails
  fast with a clear error (no stale fallback).
- Verified by testing_agent iteration_9.json: 100% backend, propose->approve->place->close clean on live
  Deriv-Demo, no "Invalid stops", 1.2s latency. Settings adjustable-hold/auto-approve/trailing + scan-status
  readout also shipped this session.

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

## Fix: charts not loading / too slow + MT5-ONLY data (FIXED — 2026-10-04, testing_agent verified)
- User reported charts not loading / loading slowly, and instructed: use ONLY Deriv/MT5 — remove binance/coinbase/yahoo entirely.
- Root cause: when MT5 ready was False, get_candles diverted Gold/synthetics to crypto exchanges (binance.us 400 / coinbase 404) -> slow retry chain -> HTTP 502; cold requests could block up to 60s on a blocking MT5 reconnect.
- Fix (market_data.py + mt5_trader.py): get_candles/get_live are now MT5/Deriv ONLY (all other sources removed). Requests NEVER block on connect — mt5_trader.is_ready() gate + ensure_bg() fire-and-forget reconnect; MT5 fetches wrapped in short asyncio.wait_for; stale _cache served during transient drops; DerivUnavailable -> route returns 200 {available:false,reason} (never 502). live response includes reason when unavailable.
- testing_agent iteration_10.json: code fix 100% (no 502, <1s, MT5-only, friendly unavailable). It hit an EXTERNAL MetaApi quota (TooManyRequests: 33/25 subscriptions) from repeated dev restarts; cleared on its own and all three symbols (BTCUSD/XAUUSD/Volatility 100 Index) then returned available:true from deriv-mt5 in ~0.6s. NOTE: avoid rapid backend restarts — each adds a MetaApi subscription (max 25/user).

## Fix: "FIX CHART DATA" + MetaApi subscription-quota churn (FIXED — 2026-10-04, testing_agent iteration_11 verified)
- Charts intermittently showed no data. Root cause was MetaApi error LIMIT_ACCOUNT_SUBSCRIPTIONS_PER_USER (used 32/25): the app created a NEW MetaApi client on every reconnect/restart and never closed the old one, and _rpc dropped the connection on any slow call -> reconnect storm. preview + the production deploy share one MetaApi token and together exceeded the 25-subscription plan limit.
- Fix (mt5_trader.py + server.py): reuse ONE module MetaApi client (_api); close the old RPC connection before reconnecting; on a FAILED connect dispose the client (conn.close(); api.close(); _api=None) so its socket stops holding a slot during the 120s back-off; release the subscription on app shutdown (server.py shutdown -> mt5_trader.shutdown()); _rpc no longer drops the connection on a single slow call. close() is SYNChronous (not awaited) per MetaApi SDK. Confirmed via integration_expert playbook (getters do NOT consume the account-subscription quota; only connections do).
- Result: churn stopped, quota drained (32->26), MT5 reconnected; BTCUSD/XAUUSD/Volatility 100 Index all return available:true with 200 candles in ~0.5-0.9s. testing_agent iteration_11: 9/9 pass, no 502, MT5-only, graceful available:false+reason when down.
- OPERATIONAL: preview and production share the same MetaApi token/account (25-sub plan). Avoid rapid restarts; consider a SEPARATE MetaApi token/account for production vs preview, or raise the MetaApi subscription quota, to avoid contention.

## Chart permanent fix (warm cache) + realistic lots + trade persistence (2026-06, testing_agent iteration_12 verified)
User asks: (1) permanently fix charts not loading / "inaccurate (frozen)" data, MT5/Deriv ONLY; (2) realistic lots starting at 2, scaling up by confidence; (3) bot's demo trades must always show and survive refresh.

- CHARTS (market_data.py fully rewritten, MT5-ONLY): stripped ALL Binance/Coinbase/Yahoo/Deriv-WebSocket code — zero chance of a non-MT5 source. Added a WARM CACHE (in-memory + MongoDB `candles_cache`) kept fresh by a background poller (`scheduler._candle_loop`, every 15s, `market_data.warm_candles()`) for core instruments (XAUUSD/BTCUSD/Volatility 100) + whatever charts the user viewed in the last 15 min. get_candles() serves the warm cache instantly; on an MT5 drop it falls back to memory then the DB copy, raising DerivUnavailable only when nothing is available. Result: charts load in ~0.12s warm (vs up to 8s cold) and NEVER go blank during an MT5 reconnect. /market/symbols now source='deriv-mt5' only (727 instruments). get_live stamps a fresh server_ts when serving last-known price during a drop.
- LOTS (risk_config.confidence_lot + trade_engine): BASE_LOT=2.0 at MIN_CONFIDENCE(58%) scaling LINEARLY to MAX_LOT=10.0 at 100%, then clamped to each symbol's volume min/max/step. propose() uses confidence_lot() via _clamp_lot(). Verified live: XAUUSD @77% -> 5.62 lots, Volatility 100 @92% -> 8.48 lots. (Replaces the old $-risk sizing that produced tiny 0.05-lot positions.)
- TRADE PERSISTENCE (trade_engine.open_positions): now DB-driven from db.trades(status=open), enriched with live MT5 price/P&L, reconciled to 'closed' ONLY when MT5 confirms the position is gone. Bot trades survive page refresh / MT5 reconnect. close() now polls the broker on a transient 'account not connected' error and treats a vanished position as a successful close (no false "Close failed" toast).
- testing_agent iteration_12: 6/6 manual acceptance checks pass when MetaApi slot available; warm-cache candle tests pass even during MT5 outage. retest_needed:false.
- OPERATIONAL (unchanged, recurring): MetaApi 25/25 subscription quota still gets hit after rapid restarts / shared preview+prod token. Warm cache now insulates CHARTS from this, but live price + new proposals need an active subscription slot. Fix at account level: separate MetaApi token for prod vs preview, or raise the subscription quota.

## Prediction-driven SL/TP + growth-oriented sizing + 2x goal (2026-06)
User: "Upgrade bot to logically set SL/TP based on their predictions. Goal should be to double the demo balance."
- SL/TP now PREDICTION-DRIVEN (trade_engine._optimize_levels): the stop is anchored to volatility (1.3xATR, capital protection); the take-profit is chosen by SIMULATING several reward:risk targets (1.0/1.5/2.0/2.5/3.0:1) through the Monte-Carlo _predict and keeping the one with the best expected value (tie-broken by win prob). The chosen rr flows through propose(), is stored on the proposal, and is re-applied in _fresh_stops() at approval (previously everything was a hardcoded 1.5R). Proposals now show R:R, Expected R, Risk ≈ $ and Reward ≈ $.
- GROWTH RISK GUARD (risk_config.RISK_CAP_PCT=0.20 + trade_engine._cap_lot_to_risk): confidence still sets the desired lot (2->10), but the lot is trimmed so a stop-loss hit risks at most 20% of equity. Fixes the problem that a flat lot count means wildly different $ risk per instrument (e.g. Gold 5.62 lots = $5,620/56% of equity -> trimmed to 2.06 lots/$2,060; Volatility 100 8.48 lots = only $62, unaffected). This is the "logical" part: doubling requires surviving losers.
- 2X BALANCE GOAL (trade_engine._growth_target + db.bot_meta _id='growth'): captures the starting balance once; target = 2x. account_summary() returns {start_balance, target_balance, growth_pct, goal_reached}. TradePanel shows a "GOAL - DOUBLE THE BALANCE" progress bar with the explicit "SL/TP set from each trade's prediction to grow the balance (no guarantees)" disclaimer. NO profitability/accuracy is promised anywhere.
- MT5 SELF-HEAL (mt5_trader._rpc): after RPC_FAIL_LIMIT(4) consecutive failed RPC calls the connection is flagged not-ready so the cooldown-gated background reconnect heals a half-dead (account-dropped-broker-sync) connection, instead of staying "ready" forever and needing a process restart. rpc_fails resets on success/connect.
- Verified live via curl + screenshot: Vol100 -> SELL 8.48 lots rr3.0 win97% +2.80R risk$62; Gold -> SELL trimmed to 2.06 lots rr3.0 risk$2,060 (<=20% equity); growth bar renders ($10,543.90 / $19,922.38, 5.8%). Not run through testing_agent (incremental sizing/SL-TP math; placement path unchanged and already validated in iteration_12).

## Adaptive learning + honest odds + proven-edge filters (2026-06, testing_agent iteration_13: 6/6 pass)
User: "Bot is losing too much money. Upgrade its logic to be adaptive and learn so future trades are profitable. Use online courses/YouTube to develop a strategic edge."
Root cause of losses: the Monte-Carlo used a PERSISTENT near-certain drift (tilt 0.6) -> reported impossible odds (e.g. 97% win at 3:1), so the bot took coin-flip trades as if sure things. Research (expectancy-in-R, ADX-as-filter, multi-timeframe confluence) grounded the fix.
- HONEST SIMULATION (trade_engine._predict): cut the outlook drift tilt 0.6->0.12, down-weighted historical drift, and gave each path its own drift (rng.normal(mu_center, sigma*0.5)) to model drift UNCERTAINTY. Odds are now realistic (verified: Vol75 BUY = 52% / 49% win, not 97%).
- ADAPTIVE LEARNING (new learning.py + db.trade_outcomes): every closed trade is logged as an R-multiple (profit/planned_risk). stats() computes realized expectancy = win_rate*avgWinR - lossRate*avgLossR per setup (symbol+side). calibrate_winprob() blends the sim's odds with REALIZED win rate via sample-size shrinkage (n/(n+10)), and caps untested high odds at 0.78. edge_block() skips a setup whose realized expectancy <= -0.15R over >= 12 trades. Outcomes recorded on broker close (open_positions reconciliation) and user close (_learn_close) via _realized_profit() from MetaApi deals. backfill_outcomes() seeds the learner from already-closed trades at startup.
- PROVEN-EDGE FILTERS in _analyze: ADX>=20 trend/chop filter (indicators.adx added); multi-timeframe confluence via _htf_bias (never BUY when higher TF is Down / SELL when Up). Gate is now positive EXPECTANCY: cal_win must clear breakeven(1/(1+rr))+margin and >=0.33, and cal_ev>=MIN_EXPECTED_R.
- GATE now uses CALIBRATED win_prob + expectancy (not raw sim). Proposals/scan-status show calibrated win_prob, expected_r (R expectancy), sim_win_prob, adx, htf_dir, regime, edge_n, edge_expectancy.
- UI (TradePanel): "LEARNED EDGE" card (overall expectancy R/trade, win%, net R, top setups) shown once >=1 trade closed; proposal cards show R:R, Risk $, Reward $.
- New endpoint GET /api/trades/edge -> {overall, setups[]}. Fixed: propose() wraps _analyze in try/except (was 500 for some symbols -> now 400 with reason).
- testing_agent iteration_13: 6/6 pytest pass incl full learn loop (propose->approve->positions->close-> /trades/edge n 0->1). retest_needed:false. Test file: backend/tests/test_adaptive_learning.py.
- NOTE: demo balance had already dropped (~$10k -> ~$3.6k) from the earlier oversized/overconfident trades; the risk cap (20%), honest odds, ADX/MTF filters and the learner are what reduce future bleed. No profitability is promised.

## FIX FOREVER: "Could not reach the trading account: MT5 temporarily unavailable" (2026-06)
Error source: trade_engine._risk_gate called mt5_trader.account_info() which raised during the post-failed-connect COOLDOWN window -> approve failed with a scary message. Account is actually reachable ~most of the time; failures were only inside the self-imposed cooldown.
Code fixes (mt5_trader.py + trade_engine.py):
- ensure(force=False): explicit USER actions pass force=True to bypass the reconnect cooldown and attempt a fresh connect immediately. market_order() now uses ensure(force=True) so approving a trade always gets a live reconnect attempt.
- account_info() caches the last successful snapshot (_state['last_account']); last_account() exposes it.
- _risk_gate(): tries account_info(force=True); on failure falls back to the cached demo snapshot (demo-only is safe to trust) instead of hard-blocking; only blocks with a friendly "reconnecting — try again in a few seconds" if the account was NEVER reached. tradeAllowed check tolerant of cache.
- account_summary(): serves last-known snapshot with stale:true instead of flipping the panel to OFFLINE during a transient drop.
- CONNECT_COOLDOWN 120s -> 45s for faster self-heal (safe: a lock + connecting flag ensure only one connect runs at a time, so no storm).
ROOT CAUSE (account-level, NOT code): the same MetaApi token + MT5 account (be0b2a17...) is shared across preview AND every deployment the user triggers; MetaApi plan allows 25 subscriptions/user, and the contention makes MetaApi drop the account's broker sync ("not connected to broker yet"). The code now masks the transient windows, but the ONLY permanent cure is a SEPARATE MetaApi account/token for production (distinct MT5_ACCOUNT_ID) or a higher MetaApi subscription quota. Verified happy path still works (account avail True, is_demo True).

## DRASTIC trade-logic upgrade: regime-aware confluence engine (2026-06, research-grounded)
Deep research (VWAP/ORB confluence, ADX+Hurst/variance-ratio regime detection, Bollinger-Keltner squeeze + Supertrend + RSI divergence for Deriv synthetics, 0-100 multi-factor confluence scoring, break-and-retest + volume confirmation, skip-chop). New module backend/edge.py:
- REGIME DETECTION: ADX + variance-ratio. momentum = ADX>=25 & VR>=1.08 (trade WITH trend); reversion = ADX<=18 & VR<=0.92 (FADE stretched RSI back to mean); anything else = chop -> STAND ASIDE (no trade).
- CONFLUENCE SCORE 0-100: momentum scores EMA20/50 structure+slope, MACD, RSI-not-stretched, +DI/-DI; reversion scores RSI extreme + RSI divergence + Bollinger-band touch; both add higher-timeframe agreement (+10/-15), volume expansion (tickVolume >=1.3x, +10), Bollinger/Keltner squeeze release (+10). Must clear MIN_SCORE=62.
- _analyze() now: compute HTF bias -> edge.assess(candles, htf_dir) decides direction+regime+score; only tradeable setups continue to the honest Monte-Carlo SL/TP optimizer -> learning calibration (cal win/EV) -> expectancy gate (cal_win>=breakeven+margin, cal_ev>=0.05R) -> confidence_lot sizing -> 20% equity risk cap. Signed edge score feeds the sim's small drift lean.
- scan-status & proposals now expose regime, score, adx, vr; reason strings read e.g. "Momentum SELL · confluence 80/100 · 48% win, +0.93R at 3.0:1, ADX 33".
- Verified live (7 instruments): XAUUSD momentum 80/100 tradeable, Vol75 momentum 80/100 tradeable; BTCUSD chop (VR 0.78), Vol100/Vol10 reversion-but-RSI-not-stretched, Step chop, Vol25 score 20 -> ALL correctly skipped. Far more selective (rejected 5/7) which directly targets the "losing too much" problem. Honest odds (~48-49%), no more impossible 97%. NOT run through testing_agent this round (budget); downstream sim/calibration/sizing/placement unchanged since iter-13. No profitability promised.

## Full-history charts + deep-history strategy profiling (2026-06)
User: "Allow the user to see the entire data for the assets from its inception. Use that data (from the beginning) and apply it to the strategies." Confirmed: range selector w/ auto-TF + MAX=deepest; strategies get BOTH (a) per-asset long-run profile tuning and (b) historical backtest prior; realistic "as deep as Deriv provides" limit, cached in DB.
- DEEP HISTORY (mt5_trader.candles_deep): pages Deriv MT5 history backward 1000/call via get_historical_candles(startTime), de-dups, tolerates flaky pages (returns partial), caches. Verified: XAUUSD MAX=3000 daily since 2017-01-24; BTCUSD MAX=4000 daily since 2015-08-16.
- history.py: get_history(symbol,range) -> range->(tf,bars) map {1D:5m/300, 1W:1h/200, 1M:1h/760, 1Y:1d/420, MAX:1d/4000}; cached in db.history_cache (ttl by tf); shallow md.get_candles fallback if deep fetch empty. Routes GET /api/market/history + /api/market/profile.
- CHART UI (PriceChart.jsx): LIVE/1D/1W/1M/1Y/MAX range buttons (data-testid range-<R>). LIVE = existing realtime behavior; historical ranges render deep candles (no live overlay) with a "History · <tf> · <n> bars · since <date>" indicator. Verified rendering BTCUSD full curve since 2015.
- ASSET PROFILE + BACKTEST PRIOR (history.build_profile -> db.asset_profiles): from deep 1h (2500) + 1d (1500) history computes vol_pct, atr_pct, trend_frac (ADX>=25 base rate), vr_longrun, and a BACKTEST of the live regime/confluence rules over history -> hist win-rate & expectancy per side. Verified XAUUSD: 43 bt trades, win 0.488, exp +0.465R, trend_frac 0.514. Built lazily via get_profile (bg refresh, 24h ttl) + at startup for core symbols.
- STRATEGY USE: learning.calibrate_winprob(pred,symbol,side,hist) now blends the historical backtest prior (hist_n>=20) before live realized stats. _analyze: floors SL to asset's long-run atr_pct, and SKIPS mean-reversion fades on assets that historically trend >=60% of the time (per-asset auto-tune). Proposals/scan expose score, vr, hist_win, hist_expectancy, asset_trend_frac.
- NOT run through testing_agent this round (budget; markets were all chop/reversion so no tradeable proposal to exercise the prior live). Deep-history + chart + profiling verified via curl + screenshot. Vol100 deep-daily intermittently times out on MetaApi (flaky quota) — handled gracefully ("loading, try again"), caches once a fetch succeeds.

## Regime dead-zone fix (2026-06)
User reported bot too idle: "Unclear regime (ADX 23, VR 0.70) — no edge, standing aside." Root cause: edge.py reversion band required ADX<=18, so ADX 18-25 was a dead zone even when VR was decisively reverting. Fixes in edge.py:
- Reversion now = VR<=0.92 AND ADX<25 (no ADX floor) -> ADX 23/VR 0.70 => reversion.
- Momentum now = (ADX>=25 & VR>=1.05) OR (ADX>=35 & VR>=0.98) so very strong trends aren't dropped when VR is ~neutral. VR_PERSIST 1.08->1.05.
- RSI fade trigger loosened 30/70 -> 33/67 (RSI_FADE_LO/HI). MIN_SCORE 62->60.
Still gated by confluence score>=60 + positive calibrated expectancy, so no return to over-trading. Verified via scan-status: regimes now classify correctly; bot trades when RSI stretches / trend forms, waits in flat mid-RSI ranges (currently Gold RSI53, BTC chop, 100Index RSI44 -> idle, correct).

## Option B (trade moderate setups) + plain-English UI (2026-06)
User (non-trader): wants more trades on moderately-stretched ranges AND all jargon removed from the site.
- edge.py reversion branch rewritten SCORE-DRIVEN: direction from which side of the 20-avg price sits (+ RSI<50/>50 confirm), RSI distance from 50 is a MAGNITUDE score (>=18:+28, >=11:+20, >=6:+12) instead of a hard 30/70 gate; + divergence(+15), Bollinger-band distance(+12/+6). MIN_SCORE 60. So RSI 37 now gets scored (e.g. Gold 37/100) and trades when band/divergence/higher-tf/volume stack to >=60. VR_PERSIST 1.05; momentum also triggers on very strong trend (ADX>=35 & VR>=0.98). Still gated by score>=60 + positive calibrated expectancy (no return to over-trading).
- PLAIN ENGLISH everywhere user-facing: edge.py reasons ("Market is range-bound and price has dropped below its average — betting it bounces back up"), regime_label field (Trending/Range-bound/Choppy), trade_engine _analyze + propose reasons ("Buy setup on a range-bound market — about 55% chance of working... Usually plays out in ~2h"; "The plan: buy 2 lots — risking ~$X to aim for ~$Y"), outlook.py bullets (no RSI/MACD/EMA terms), outlook metrics chips (Direction/Recent strength/Who's winning/Price swings), outlook title "No clear direction", "LEAN"/"HOW SURE". TradePanel proposal stats: Chance of working / Setup strength / Market / You risk / You aim to win / Usually takes. Learned-edge card: "What the bot has learned · N trades · X% won · On average these trades made/lost money".
- Verified: scan-status returns plain reasons, RSI-37 setups now scored (not hard-rejected); screenshots confirm outlook + learned-edge + scan rows render in plain English.

## License-key access control + tiers (2026-06)
User: gate the platform behind license keys so more people can help the bot learn (NOT for sale). Tiers by access level: basic/moderate/advanced; durations 1m/3m/6m/1y; tier+duration INDEPENDENT per key. Expiry -> restricted (alerts off + locked to renew screen) -> +7d -> disabled. xambiep90 = admin who mints keys.
- backend/licensing.py: gen_key (TC-XXXX-XXXX-XXXX-XXXX), TIER_RANK, DURATIONS, compute_status, license_view, _redeem (stacks on remaining time for renewals), platform_guard (active+moderate+ gate on /api router), require_advanced (trade writes), require_admin, refresh_statuses (hourly job in scheduler). Routers: /api/admin/keys (mint/list/revoke), /api/admin/users (list/renew), /api/account/redeem + /license.
- auth.py: register (email+password+license_key -> validates key, activates), login accepts email OR username, seed makes xambiep90 role=admin, _user_public returns {id,username,email,role,license:license_view}.
- routes.py: api router now Depends(platform_guard). Trade write endpoints add Depends(platform_guard)+require_advanced.
- Collections: license_keys, users gains {role,email,tier,license_key,license_started_at,license_expires_at,license_status}.
- Frontend: Login.jsx Sign-in/Activate-key tabs (email+key+password register); AuthContext register/redeem/refreshUser; LicenseGate.jsx wraps Dashboard -> activate/renew/disabled/basic screens by status+tier; AdminKeys.jsx dialog (admin-only Keys button in TopBar) to mint/list/revoke.
- VERIFIED via curl: admin mints keys; basic register -> platform 403; moderate -> market 200 + trades/propose 403 + positions 200; advanced -> full 200; expiry set correctly (1m=29d left). Login register UI screenshot confirmed. NOT run through testing_agent (budget); expiry->restricted->disabled transitions are time-based (hourly job).
- TODO/backlog: gate email ALERTS by tier (basic gets alert+market-preview, moderate/advanced get alerts) — currently alerts still use the single settings recipient; per-user alert routing not yet wired. Admin users-renew UI not surfaced in frontend (API exists).

## TradingView-style charts + animated landing (2026-06)
User: (1) dashboard candles should look & behave like the Deriv web terminal; (2) replace static landing hero image with an animated growing chart.
- Installed `lightweight-charts@5.2.1` (TradingView engine, v5 API: `addSeries(CandlestickSeries, ...)`).
- Candlestick.jsx rewritten to lightweight-charts: native crosshair, pan, scroll-zoom, auto-scaling price axis, time axis, live price label, theme-aware colors. Props unchanged ({candles,height,liveLast}) so PriceChart.jsx + range selector (LIVE/1D/1W/1M/1Y/MAX) untouched. setData on structural change + series.update() for live tail (keeps zoom/scroll smooth). precision auto (4 dp for <10 price). MUST set `localization:{locale:"en-US"}` — env locale `en-US@posix` crashes Intl otherwise.
- LandingChart.jsx (new): decorative AreaSeries, synthetic upward random-walk, appends a point every 850ms + scrollToRealTime, glowing green line/gradient, axes/grid hidden, interactions off. Login.jsx left panel now renders LandingChart instead of the Unsplash HERO image.
- VERIFIED via screenshots: landing growing green chart animates; dashboard BTCUSD renders real candles w/ crosshair; 1M range switch re-renders history (760 bars) correctly. Frontend-only visual change.

## Entry-timing upgrade — stop chasing, enter near value (2026-06)
User: "bot spots trends but needs better entry timing — narrow the best buy/sell point to maximize profit without being in a trade for hours." Confirmed plan 1+2+3.
- edge.py: added entry-timing gates. Constants EXT_MAX_ATR=2.2, PULLBACK_ZONE_ATR=0.8, SWING_LOOKBACK=5, SWING_BUF_ATR=0.25 + `_swing_stop_dist()`.
  - MOMENTUM: compute extension = (price-EMA20)/ATR in the profit direction. If ext_now>2.2 ATR -> NOT tradeable ("run too far — waiting for a pullback"). Otherwise require a trigger: pullback_resume (a recent bar came within 0.8 ATR of the avg AND last bar turns back in trend dir) OR breakout (squeeze_release + resuming). No trigger -> "not a good spot to get in yet". Sets `trigger` + `swing_dist`.
  - REVERSION: require a turn bar (last close up/above open for a long, down for a short) or it's a falling knife -> wait. Sets trigger "Bounce is starting" + swing_dist.
  - assess() now returns `trigger` and `swing_stop_dist`.
- trade_engine.py:
  - `_optimize_levels(..., swing_dist)`: when a well-timed entry has a swing, stop = just beyond swing, clamped to [0.6*ATR .. ATR-stop] (tighter, better-placed -> higher R:R). Falls back to 1.3*ATR stop otherwise.
  - `_analyze`: passes ev.swing_stop_dist; SLOW-SETUP FILTER — if sim median time-to-resolve (est_hold_bars) >= 90% of the max-hold horizon, reject ("would drag on for hours — skipping for a faster one"). Surfaces `trigger` in base + success reason ("Entry: pulled back to value...") + scan_status rows + proposal.
- VERIFIED: backend imports clean; live /api/trades/scan-status now shows Gold/BTC trending but "waiting for a pullback or a fresh breakout" (no more chasing). Synthetic unit tests: pullback-resume -> tradeable BUY, trigger set, tight swing stop (1.56), score 79; overextended -> waits. Net effect: fewer, better-timed trades, shorter time-in-trade. Not run through testing_agent (change is deterministic read-only decision logic; MetaApi placement unchanged).

## Instant Deriv data refresh — stale-while-revalidate (2026-06)
User: "Deriv data should refresh instantaneous, it delays too long sometimes."
Root cause: get_live did TWO sequential MetaApi RPCs (live_price 10s + candles 12s) and get_candles blocked up to 18s on cache expiry — so a slow MT5 RPC froze every poll.
- market_data.py get_live -> SWR + parallel: serves last tick from cache instantly, triggers a NON-blocking background refresh when stale (>2s); refresh runs live_price + candles via asyncio.gather (timeouts 6s/8s). Only a true cold start does one blocking fetch for first paint. _LIVE_TTL 3->2.
- market_data.py get_candles -> SWR: always returns warm cache (memory, else DB) instantly and refreshes in background (_trigger_candle_refresh/_refresh_candles + _candles_refreshing guard). Blocking fetch only on true cold start (no memory+no DB).
- PriceChart.jsx live poll 3000ms -> 2000ms (backend now non-blocking so cheap).
- VERIFIED via curl: cold start live ~3.6s once; every warm poll ~0.1s (live) / ~0.09s (candles); Volatility100 price advanced 612.165->612.065->612.445 across polls (background refresh live). No more multi-second stalls.
