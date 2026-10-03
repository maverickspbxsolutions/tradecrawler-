import { useQuery } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, ArrowDownRight, Mail, History, Radar, Loader2 } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, fmtUsd, fmtTime } from "@/lib/api";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";

function PlanLine({ s }) {
  const plan = s.plan;
  const coin = s.symbol.replace("USDT", "");
  if (s.side === "BUY") {
    if (plan?.action === "BUY") {
      return (
        <span>
          Buy ~<span className="num">${plan.capital}</span> of {coin} at{" "}
          <span className="num">{fmtUsd(plan.entry, plan.entry < 10 ? 4 : 2)}</span> → aim{" "}
          <span className="num text-buy">{fmtUsd(plan.target_price, plan.target_price < 10 ? 4 : 2)}</span>{" "}
          (≈ <span className="num text-buy">+${plan.est_profit}</span>)
        </span>
      );
    }
    return <span>Buy signal for {coin} at {fmtUsd(s.price, s.price < 10 ? 4 : 2)}</span>;
  }
  return (
    <span>
      Sell your {coin} now at <span className="num">{fmtUsd(s.price, s.price < 10 ? 4 : 2)}</span>
      {s.pnl != null && <> · result <span className={`num ${s.pnl >= 0 ? "text-buy" : "text-sell"}`}>${s.pnl}</span></>}
    </span>
  );
}

export function SignalFeed() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["signals"],
    queryFn: async () => (await api.get("/signals?limit=60")).data,
    refetchInterval: 10000,
  });

  const scanAll = useMutation({
    mutationFn: () => api.post("/scan-all"),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["signals"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
      const n = r.data.signals?.length || 0;
      toast.message(n ? `Found ${n} new trade alert(s)` : "Nothing worth trading right now — TradeCrawler keeps watching");
    },
  });

  const signals = data || [];

  return (
    <div className="border border-border/40 bg-card rounded-sm flex flex-col" data-testid="signal-feed">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/40">
        <div className="text-xs font-mono uppercase tracking-[0.15em] text-muted-foreground">Trade Alerts</div>
        <div className="flex items-center gap-3">
          <span className="text-[10px] font-mono text-muted-foreground">{signals.length} shown</span>
          <Button size="sm" variant="outline" className="h-7 rounded-sm" onClick={() => scanAll.mutate()} disabled={scanAll.isPending} data-testid="scan-all-btn">
            {scanAll.isPending ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Radar className="h-3.5 w-3.5 mr-1" />}
            Check now
          </Button>
        </div>
      </div>
      <ScrollArea className="h-[300px]">
        {signals.length === 0 ? (
          <div className="h-[300px] grid place-items-center text-xs text-muted-foreground font-mono px-6 text-center">
            No alerts yet. TradeCrawler only acts on finished price bars — it's watching 24/7.
          </div>
        ) : (
          <div>
            {signals.map((s) => {
              const buy = s.side === "BUY";
              return (
                <div
                  key={s.id}
                  className={`flex items-start gap-3 px-4 py-2.5 border-b border-border/30 hover:bg-accent ${buy ? "flash-buy" : "flash-sell"}`}
                  data-testid="signal-row"
                >
                  <div className={`h-7 w-7 shrink-0 grid place-items-center rounded-sm ${buy ? "bg-buy/10 text-buy" : "bg-sell/10 text-sell"}`}>
                    {buy ? <ArrowUpRight className="h-4 w-4" /> : <ArrowDownRight className="h-4 w-4" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className={`num text-sm font-semibold ${buy ? "text-buy" : "text-sell"}`}>{s.side}</span>
                      <span className="num text-sm">{s.symbol.replace("USDT", "")}</span>
                      <span className="text-[10px] font-mono px-1 py-0.5 border border-border/60 rounded-sm text-muted-foreground">{s.timeframe}</span>
                      {s.source === "historical" && (
                        <span className="text-[9px] font-mono flex items-center gap-1 text-muted-foreground">
                          <History className="h-3 w-3" /> past example
                        </span>
                      )}
                      {s.alert?.emailed && (
                        <span className="text-[9px] font-mono flex items-center gap-1 text-primary">
                          <Mail className="h-3 w-3" /> emailed
                        </span>
                      )}
                    </div>
                    <div className="text-[12px] text-foreground/90 mt-0.5 leading-snug">
                      <PlanLine s={s} />
                    </div>
                    <div className="text-[10px] text-muted-foreground font-mono mt-0.5">{s.strategy_name} · {fmtTime(s.created_at)}</div>
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
