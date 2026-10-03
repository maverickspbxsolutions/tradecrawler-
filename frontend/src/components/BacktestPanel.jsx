import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from "recharts";
import { toast } from "sonner";
import { Play, Loader2, TrendingUp, Trophy } from "lucide-react";
import { api, fmtUsd, fmtPct, pnlColor, fmtTime } from "@/lib/api";
import { Button } from "@/components/ui/button";

function M({ label, value, cls }) {
  return (
    <div className="border border-border/40 bg-background px-3 py-2 rounded-sm">
      <div className="text-[9px] font-mono uppercase tracking-[0.1em] text-muted-foreground">{label}</div>
      <div className={`num text-base ${cls || ""}`}>{value}</div>
    </div>
  );
}

export function BacktestPanel({ strategy }) {
  const qc = useQueryClient();
  const sid = strategy?.id;

  const { data: bt } = useQuery({
    queryKey: ["backtest", sid],
    queryFn: async () => {
      try {
        return (await api.get(`/strategies/${sid}/backtest`)).data;
      } catch {
        return null;
      }
    },
    enabled: !!sid,
  });

  const run = useMutation({
    mutationFn: () => api.post(`/strategies/${sid}/backtest`, { limit: 500 }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["backtest", sid] });
      qc.invalidateQueries({ queryKey: ["strategies"] });
      toast.success("Backtest complete");
    },
    onError: (e) => toast.error(e.response?.data?.detail || "Backtest failed"),
  });

  const curve = (bt?.equity_curve || []).map((p) => ({
    t: p.t,
    Strategy: p.equity,
    "Buy & Hold": p.benchmark,
  }));

  return (
    <div className="border border-border/40 bg-card rounded-sm" data-testid="backtest-panel">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/40">
        <div className="text-xs font-mono uppercase tracking-[0.15em] text-muted-foreground flex items-center gap-2">
          <TrendingUp className="h-3.5 w-3.5" /> Past performance · {strategy?.name || "—"}
        </div>
        <Button
          size="sm"
          className="h-7 rounded-sm"
          variant="outline"
          disabled={!sid || run.isPending}
          onClick={() => run.mutate()}
          data-testid="run-backtest-btn"
        >
          {run.isPending ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Play className="h-3.5 w-3.5 mr-1" />}
          Re-test
        </Button>
      </div>

      {!bt ? (
        <div className="h-[320px] grid place-items-center text-xs text-muted-foreground font-mono px-6 text-center">
          No test yet — tap “Re-test” to see how this crawler would have done on recent prices.
        </div>
      ) : (
        <div className="p-4 space-y-4">
          <div className="h-[200px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={curve} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
                <XAxis dataKey="t" hide />
                <YAxis
                  orientation="right"
                  width={58}
                  tick={{ fontSize: 10, fontFamily: "Azeret Mono", fill: "#8A8A93" }}
                  tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`}
                  domain={["auto", "auto"]}
                />
                <Tooltip
                  contentStyle={{ background: "#121215", border: "1px solid #1F1F24", borderRadius: 2, fontFamily: "Azeret Mono", fontSize: 12 }}
                  labelFormatter={(t) => fmtTime(new Date(t).toISOString())}
                  formatter={(v, n) => [fmtUsd(v), n]}
                />
                <Legend wrapperStyle={{ fontSize: 10, fontFamily: "Azeret Mono" }} />
                <Line type="monotone" dataKey="Strategy" stroke="#00E5FF" strokeWidth={1.6} dot={false} />
                <Line type="monotone" dataKey="Buy & Hold" stroke="#565660" strokeWidth={1.2} strokeDasharray="4 3" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className={`flex items-center gap-2 text-xs font-mono px-3 py-2 rounded-sm border ${bt.beats_benchmark ? "border-buy/40 text-buy bg-buy/5" : "border-sell/40 text-sell bg-sell/5"}`} data-testid="benchmark-verdict">
            <Trophy className="h-3.5 w-3.5" />
            {bt.beats_benchmark ? "Better than just buying and holding" : "Worse than just buying and holding"} · made {fmtPct(bt.total_return_pct)} vs {fmtPct(bt.benchmark_return_pct)}
          </div>

          <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
            <M label="Avg per trade" value={fmtPct(bt.expectancy_pct)} cls={pnlColor(bt.expectancy_pct)} />
            <M label="Win vs loss" value={bt.risk_reward} />
            <M label="Worst drop" value={`${bt.max_drawdown_pct}%`} cls="text-sell" />
            <M label="Trades" value={bt.num_trades} />
            <M label="Avg win" value={fmtPct(bt.avg_win_pct)} cls="text-buy" />
            <M label="Avg loss" value={fmtPct(bt.avg_loss_pct)} cls="text-sell" />
            <M label="Win rate" value={`${bt.win_rate}%`} />
            <M label="Ended with" value={fmtUsd(bt.final_equity)} />
          </div>
          <div className="text-[10px] text-muted-foreground font-mono">
            Based on a pretend $10,000 over the last {bt.candles} price bars, including trading fees &amp; slippage. Past results don't guarantee the future — this just checks the crawler has a real edge.
          </div>
        </div>
      )}
    </div>
  );
}
