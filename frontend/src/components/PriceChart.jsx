import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, fmtUsd, fmtPct, pnlColor } from "@/lib/api";
import { Candlestick } from "@/components/Candlestick";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const TFS = ["1m", "5m", "15m", "1h", "4h", "1d"];

export function PriceChart({ symbol, tf, onTfChange }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const { data } = useQuery({
    queryKey: ["candles", symbol, tf],
    queryFn: async () => (await api.get(`/market/candles?symbol=${symbol}&timeframe=${tf}&limit=120`)).data,
    refetchInterval: 15000,
    enabled: !!symbol,
  });

  const live = useQuery({
    queryKey: ["live", symbol, tf],
    queryFn: async () => (await api.get(`/market/live?symbol=${symbol}&timeframe=${tf}`)).data,
    refetchInterval: 5000,
    enabled: !!symbol,
    retry: 1,
  });

  const closed = data?.candles || [];
  const liveCandle = live.data?.candle || null;

  let display = closed;
  let hasLive = false;
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

  const last = live.data?.price != null ? live.data.price : (display.length ? display[display.length - 1].c : null);
  const first = display.length ? display[0].c : null;
  const changePct = last != null && first ? ((last - first) / first) * 100 : null;
  const up = changePct != null && changePct >= 0;

  const liveOk = !live.isError && live.data?.price != null;
  const ageSec = live.dataUpdatedAt ? Math.max(0, Math.round((now - live.dataUpdatedAt) / 1000)) : null;

  return (
    <div className="border border-border/40 bg-card rounded-sm" data-testid="price-chart">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/40">
        <div className="flex items-baseline gap-3 flex-wrap">
          <span className="num text-lg font-semibold">{symbol}</span>
          <span className="num text-xl" style={{ color: up ? "#00E676" : "#FF3366" }} data-testid="price-last">
            {last != null ? fmtUsd(last, last < 10 ? 4 : 2) : "—"}
          </span>
          {changePct != null && (
            <span className={`num text-xs ${pnlColor(changePct)}`}>{fmtPct(changePct)}</span>
          )}
          <span
            className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-wider"
            data-testid="live-indicator"
            title={liveOk ? `Updated ${ageSec}s ago` : "Live feed delayed"}
          >
            <span
              className="inline-block h-1.5 w-1.5 rounded-full"
              style={{ background: liveOk ? "#00E676" : "#8A8A93", animation: liveOk ? "pulse 1.2s infinite" : "none" }}
            />
            <span style={{ color: liveOk ? "#00E676" : "#8A8A93" }}>
              {liveOk ? `Live · ${ageSec ?? 0}s ago` : "Delayed"}
            </span>
          </span>
        </div>
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
      </div>
      <div className="px-3 py-2">
        <Candlestick candles={display} height={240} liveLast={hasLive} />
      </div>
    </div>
  );
}
