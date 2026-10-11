import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, fmtUsd, fmtPct, pnlColor } from "@/lib/api";
import { Candlestick } from "@/components/Candlestick";
import { useChartTheme } from "@/lib/useChartTheme";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const TFS = ["1m", "5m", "15m", "1h", "4h", "1d"];
const RANGES = ["LIVE", "1D", "1W", "1M", "1Y", "MAX"];

function sinceLabel(ms) {
  if (!ms) return "";
  const d = new Date(ms);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function PriceChart({ symbol, tf, onTfChange, strategies = [], selectedId, onSelectAsset }) {
  const theme = useChartTheme();
  const [now, setNow] = useState(Date.now());
  const [range, setRange] = useState("LIVE");
  const isLive = range === "LIVE";

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const { data } = useQuery({
    queryKey: ["candles", symbol, tf],
    queryFn: async () => (await api.get(`/market/candles?symbol=${symbol}&timeframe=${tf}&limit=120`)).data,
    refetchInterval: 15000,
    enabled: !!symbol && isLive,
  });

  const live = useQuery({
    queryKey: ["live", symbol, tf],
    queryFn: async () => (await api.get(`/market/live?symbol=${symbol}&timeframe=${tf}`)).data,
    refetchInterval: 2000,
    enabled: !!symbol && isLive,
    retry: 1,
  });

  const history = useQuery({
    queryKey: ["history", symbol, range],
    queryFn: async () =>
      (await api.get(`/market/history?symbol=${encodeURIComponent(symbol)}&range=${range}`)).data,
    enabled: !!symbol && !isLive,
    staleTime: 5 * 60 * 1000,
    retry: 2,
  });

  // ---- assemble the series ----
  let display, hasLive, unavailable, reason, histInfo;
  if (isLive) {
    const closed = data?.candles || [];
    unavailable = data && data.available === false;
    reason = data?.reason;
    const liveCandle = live.data?.candle || null;
    display = closed;
    hasLive = false;
    if (closed.length && liveCandle) {
      const lastT = closed[closed.length - 1].t;
      if (liveCandle.t > lastT) {
        display = [...closed, liveCandle];
        hasLive = true;
      } else if (liveCandle.t === lastT) {
        display = [...closed.slice(0, -1), liveCandle];
        hasLive = true;
      }
    }
  } else {
    display = history.data?.candles || [];
    unavailable = history.data && history.data.available === false;
    reason = history.data?.reason || "Deep history is loading from Deriv — try again in a moment.";
    hasLive = false;
    histInfo = history.data;
  }

  const last = isLive
    ? live.data?.price != null
      ? live.data.price
      : display.length
        ? display[display.length - 1].c
        : null
    : display.length
      ? display[display.length - 1].c
      : null;
  const first = display.length ? display[0].c : null;
  const changePct = last != null && first ? ((last - first) / first) * 100 : null;
  const up = changePct != null && changePct >= 0;

  const liveOk = isLive && !unavailable && !live.isError && live.data?.price != null;
  const ageSec = live.dataUpdatedAt ? Math.max(0, Math.round((now - live.dataUpdatedAt) / 1000)) : null;

  return (
    <div className="border border-border/40 bg-card rounded-sm" data-testid="price-chart">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/40">
        <div className="flex items-baseline gap-3 flex-wrap">
          {strategies.length > 0 && onSelectAsset ? (
            <Select value={selectedId || undefined} onValueChange={onSelectAsset}>
              <SelectTrigger
                className="h-8 w-auto min-w-[150px] gap-2 rounded-sm border-border/50 bg-muted/30 px-2.5 text-lg font-semibold"
                data-testid="asset-switcher"
              >
                <SelectValue placeholder={symbol} />
              </SelectTrigger>
              <SelectContent className="max-h-80">
                {strategies.map((s) => (
                  <SelectItem key={s.id} value={s.id} className="text-sm" data-testid={`asset-option-${s.symbol}`}>
                    <span className="font-semibold">{s.name}</span>
                    <span className="num text-[11px] text-muted-foreground ml-2">{s.symbol}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <span className="num text-lg font-semibold">{symbol}</span>
          )}
          <span className="num text-xl" style={{ color: up ? theme.buy : theme.sell }} data-testid="price-last">
            {last != null ? fmtUsd(last, last < 10 ? 4 : 2) : "—"}
          </span>
          {changePct != null && (
            <span className={`num text-xs ${pnlColor(changePct)}`}>{fmtPct(changePct)}</span>
          )}
          {isLive ? (
            <span
              className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-wider"
              data-testid="live-indicator"
              title={liveOk ? `Updated ${ageSec}s ago` : "Live feed delayed"}
            >
              <span
                className="inline-block h-1.5 w-1.5 rounded-full"
                style={{ background: liveOk ? theme.buy : theme.muted, animation: liveOk ? "pulse 1.2s infinite" : "none" }}
              />
              <span style={{ color: liveOk ? theme.buy : theme.muted }}>
                {liveOk ? `Live · ${ageSec ?? 0}s ago` : unavailable ? "Unavailable" : "Delayed"}
              </span>
            </span>
          ) : (
            <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground" data-testid="history-indicator">
              {history.isFetching
                ? "Loading history…"
                : histInfo?.available
                  ? `History · ${histInfo.timeframe} · ${histInfo.count} bars · since ${sinceLabel(histInfo.oldest)}`
                  : "History unavailable"}
            </span>
          )}
        </div>
        {isLive && (
          <Select value={tf} onValueChange={onTfChange}>
            <SelectTrigger className="h-7 w-20 rounded-sm text-xs font-mono" data-testid="price-tf-select">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TFS.map((t) => (
                <SelectItem key={t} value={t} className="text-xs font-mono">
                  {t}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      {/* range selector */}
      <div className="flex items-center gap-1 px-3 pt-2.5" data-testid="range-selector">
        {RANGES.map((r) => (
          <button
            key={r}
            onClick={() => setRange(r)}
            data-testid={`range-${r}`}
            className={`px-2.5 py-1 rounded-sm text-[11px] font-mono uppercase tracking-wider transition-colors ${
              range === r
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
            }`}
          >
            {r === "MAX" ? "Max" : r}
          </button>
        ))}
        {!isLive && histInfo?.available && (
          <span className="ml-auto text-[10px] font-mono text-muted-foreground">
            {histInfo.has_more || histInfo.total_available > histInfo.count
              ? `${histInfo.total_available} bars available`
              : "full history"}
          </span>
        )}
      </div>

      <div className="px-3 py-2">
        {unavailable && !display.length ? (
          <div className="grid place-items-center text-xs text-muted-foreground font-mono text-center px-4" style={{ height: 240 }} data-testid="chart-unavailable">
            {reason || "Data for this market is temporarily unavailable."}
          </div>
        ) : (
          <Candlestick candles={display} height={240} liveLast={hasLive} />
        )}
      </div>
    </div>
  );
}
