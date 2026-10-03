import { useState } from "react";
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

export function PriceChart({ symbol, timeframe }) {
  const [tf, setTf] = useState(timeframe || "1h");

  const { data } = useQuery({
    queryKey: ["candles", symbol, tf],
    queryFn: async () => (await api.get(`/market/candles?symbol=${symbol}&timeframe=${tf}&limit=120`)).data,
    refetchInterval: 30000,
    enabled: !!symbol,
  });

  const candles = data?.candles || [];
  const last = candles.length ? candles[candles.length - 1].c : null;
  const first = candles.length ? candles[0].c : null;
  const changePct = last != null && first ? ((last - first) / first) * 100 : null;
  const up = changePct != null && changePct >= 0;

  return (
    <div className="border border-border/40 bg-card rounded-sm" data-testid="price-chart">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/40">
        <div className="flex items-baseline gap-3">
          <span className="num text-lg font-semibold">{symbol}</span>
          <span className="num text-xl" style={{ color: up ? "#00E676" : "#FF3366" }}>
            {last != null ? fmtUsd(last, last < 10 ? 4 : 2) : "—"}
          </span>
          {changePct != null && (
            <span className={`num text-xs ${pnlColor(changePct)}`}>{fmtPct(changePct)}</span>
          )}
        </div>
        <Select value={tf} onValueChange={setTf}>
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
        <Candlestick candles={candles} height={240} />
      </div>
    </div>
  );
}
