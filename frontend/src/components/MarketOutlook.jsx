import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { TrendingUp, TrendingDown, Minus, Activity } from "lucide-react";

const DIR = {
  Up: { color: "#00E676", icon: TrendingUp, label: "Leaning Up", bg: "rgba(0,230,118,0.10)", border: "rgba(0,230,118,0.4)" },
  Down: { color: "#FF3366", icon: TrendingDown, label: "Leaning Down", bg: "rgba(255,51,102,0.10)", border: "rgba(255,51,102,0.4)" },
  Neutral: { color: "#8A8A93", icon: Minus, label: "Range / Unclear", bg: "rgba(138,138,147,0.10)", border: "rgba(138,138,147,0.35)" },
};

function Chip({ label, value, tone }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-sm border border-border/40 bg-background/40 px-2.5 py-1.5" data-testid={`outlook-chip-${label.toLowerCase().replace(/\s+/g, "-")}`}>
      <span className="text-[9px] uppercase tracking-wider text-muted-foreground font-mono">{label}</span>
      <span className="num text-xs font-semibold" style={tone ? { color: tone } : undefined}>{value}</span>
    </div>
  );
}

export function MarketOutlook({ symbol, tf }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["outlook", symbol, tf],
    queryFn: async () => (await api.get(`/market/outlook?symbol=${symbol}&timeframe=${tf}`)).data,
    refetchInterval: 15000,
    enabled: !!symbol,
    retry: 1,
  });

  const d = DIR[data?.direction] || DIR.Neutral;
  const Icon = d.icon;
  const m = data?.metrics || {};
  const slopeTone = m.trend_slope_pct > 0 ? "#00E676" : m.trend_slope_pct < 0 ? "#FF3366" : undefined;
  const macdTone = m.macd_hist > 0 ? "#00E676" : m.macd_hist < 0 ? "#FF3366" : undefined;
  const changeTone = m.recent_change_pct > 0 ? "#00E676" : m.recent_change_pct < 0 ? "#FF3366" : undefined;

  return (
    <div className="border border-border/40 bg-card rounded-sm" data-testid="market-outlook">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/40">
        <div className="flex items-center gap-2">
          <Activity className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-xs font-mono uppercase tracking-wider">Market Outlook</span>
          <span className="num text-[11px] text-muted-foreground">{symbol} · {tf}</span>
        </div>
        <span className="text-[9px] font-mono uppercase tracking-wider text-muted-foreground">reads live every 15s</span>
      </div>

      <div className="p-4 space-y-3">
        {isError || (data && data.available === false) ? (
          <p className="text-xs text-muted-foreground" data-testid="outlook-error">
            {data?.reason || error?.response?.data?.detail || "Market data unavailable for this instrument right now."}
          </p>
        ) : isLoading || !data ? (
          <p className="text-xs text-muted-foreground font-mono">Reading the market…</p>
        ) : (
          <>
            <div
              className="flex items-center justify-between rounded-sm px-3 py-2.5"
              style={{ background: d.bg, border: `1px solid ${d.border}` }}
            >
              <div className="flex items-center gap-2.5">
                <Icon className="h-6 w-6" style={{ color: d.color }} />
                <div className="flex flex-col">
                  <span className="text-lg font-semibold leading-none" style={{ color: d.color }} data-testid="outlook-direction">
                    {d.label}
                  </span>
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-mono mt-1">
                    bias score {data.score > 0 ? "+" : ""}{data.score}
                  </span>
                </div>
              </div>
              <div className="flex flex-col items-end">
                <span className="num text-2xl font-bold" style={{ color: d.color }} data-testid="outlook-confidence">
                  {data.confidence}%
                </span>
                <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-mono">confidence</span>
              </div>
            </div>

            <div className="h-1.5 w-full rounded-full bg-background/60 overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-500"
                style={{ width: `${data.confidence}%`, background: d.color }}
                data-testid="outlook-confidence-bar"
              />
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <Chip label="Trend Slope" value={m.trend_slope_pct != null ? `${m.trend_slope_pct}%` : "—"} tone={slopeTone} />
              <Chip label="RSI" value={m.rsi ?? "—"} />
              <Chip label="MACD" value={m.macd_hist ?? "—"} tone={macdTone} />
              <Chip label="Volatility" value={m.volatility_label || "—"} />
            </div>

            <ul className="space-y-1.5" data-testid="outlook-reasons">
              {(data.reasons || []).map((r, i) => (
                <li key={i} className="flex gap-2 text-xs text-foreground/90">
                  <span className="mt-1 h-1 w-1 rounded-full shrink-0" style={{ background: d.color }} />
                  <span>{r}</span>
                </li>
              ))}
            </ul>

            <p className="text-[10px] text-muted-foreground font-mono pt-1 border-t border-border/40">
              {data.disclaimer}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
