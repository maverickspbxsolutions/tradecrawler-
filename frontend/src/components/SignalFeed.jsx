import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, ArrowDownRight, Mail, History } from "lucide-react";
import { api, fmtUsd, fmtTime, fmtPct, pnlColor } from "@/lib/api";
import { ScrollArea } from "@/components/ui/scroll-area";

export function SignalFeed() {
  const { data } = useQuery({
    queryKey: ["signals"],
    queryFn: async () => (await api.get("/signals?limit=60")).data,
    refetchInterval: 12000,
  });

  const signals = data || [];

  return (
    <div className="border border-border/40 bg-card rounded-sm flex flex-col" data-testid="signal-feed">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/40">
        <div className="text-xs font-mono uppercase tracking-[0.15em] text-muted-foreground">Signal Feed</div>
        <div className="text-[10px] font-mono text-muted-foreground">{signals.length} signals</div>
      </div>
      <ScrollArea className="h-[300px]">
        {signals.length === 0 ? (
          <div className="h-[300px] grid place-items-center text-xs text-muted-foreground font-mono">
            No signals yet — the engine fires on closed candles only.
          </div>
        ) : (
          <div>
            {signals.map((s) => {
              const buy = s.side === "BUY";
              return (
                <div
                  key={s.id}
                  className={`flex items-center gap-3 px-4 py-2.5 border-b border-border/30 hover:bg-[#1A1A1F] ${
                    buy ? "flash-buy" : "flash-sell"
                  }`}
                  data-testid="signal-row"
                >
                  <div
                    className={`h-7 w-7 shrink-0 grid place-items-center rounded-sm ${
                      buy ? "bg-buy/10 text-buy" : "bg-sell/10 text-sell"
                    }`}
                  >
                    {buy ? <ArrowUpRight className="h-4 w-4" /> : <ArrowDownRight className="h-4 w-4" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className={`num text-sm font-semibold ${buy ? "text-buy" : "text-sell"}`}>{s.side}</span>
                      <span className="num text-sm">{s.symbol}</span>
                      <span className="text-[10px] font-mono px-1 py-0.5 border border-border/60 rounded-sm text-muted-foreground">
                        {s.timeframe}
                      </span>
                      {s.source === "historical" && (
                        <span className="text-[9px] font-mono flex items-center gap-1 text-muted-foreground">
                          <History className="h-3 w-3" /> backfill
                        </span>
                      )}
                      {s.alert?.emailed && <Mail className="h-3 w-3 text-primary" title="Alert emailed" />}
                    </div>
                    <div className="text-[11px] text-muted-foreground truncate">{s.strategy_name} · {s.reason}</div>
                  </div>
                  <div className="text-right shrink-0">
                    <div className="num text-sm">{fmtUsd(s.price, s.price < 10 ? 4 : 2)}</div>
                    {s.return_pct != null ? (
                      <div className={`num text-[11px] ${pnlColor(s.return_pct)}`}>{fmtPct(s.return_pct)}</div>
                    ) : (
                      <div className="text-[10px] text-muted-foreground font-mono">{fmtTime(s.candle_time)}</div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </ScrollArea>
    </div>
  );
}
