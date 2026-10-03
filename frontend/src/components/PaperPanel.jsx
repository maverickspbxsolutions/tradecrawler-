import { useQuery } from "@tanstack/react-query";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { Wallet, Activity } from "lucide-react";
import { api, fmtUsd, fmtPct, pnlColor, fmtTime } from "@/lib/api";
import { ScrollArea } from "@/components/ui/scroll-area";

function M({ label, value, cls }) {
  return (
    <div className="border border-border/40 bg-background px-3 py-2 rounded-sm">
      <div className="text-[9px] font-mono uppercase tracking-[0.1em] text-muted-foreground">{label}</div>
      <div className={`num text-base ${cls || ""}`}>{value}</div>
    </div>
  );
}

export function PaperPanel({ strategy }) {
  const sid = strategy?.id;
  const { data } = useQuery({
    queryKey: ["paper", sid],
    queryFn: async () => (await api.get(`/paper/${sid}`)).data,
    enabled: !!sid,
    refetchInterval: 20000,
  });

  const st = data?.state || {};
  const trades = data?.trades || [];
  const curve = (data?.equity_curve || []).map((p, i) => ({ i, equity: p.equity }));
  const inPos = st.position === "long";
  const pnl = st.realized_pnl ?? 0;

  return (
    <div className="border border-border/40 bg-card rounded-sm" data-testid="paper-panel">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/40">
        <div className="text-xs font-mono uppercase tracking-[0.15em] text-muted-foreground flex items-center gap-2">
          <Wallet className="h-3.5 w-3.5" /> Paper Trading · {strategy?.name || "—"}
        </div>
        <span
          className={`text-[10px] font-mono uppercase px-2 py-0.5 rounded-sm border ${
            inPos ? "border-buy/50 text-buy" : "border-border text-muted-foreground"
          }`}
          data-testid="paper-position"
        >
          {inPos ? "● in position" : "flat"}
        </span>
      </div>

      <div className="p-4 space-y-4">
        <div className="grid grid-cols-3 gap-2">
          <M label="Equity" value={fmtUsd(st.equity ?? data?.initial ?? 10000)} />
          <M label="Realized P&L" value={fmtUsd(pnl)} cls={pnlColor(pnl)} />
          <M label="Closed Trades" value={st.num_trades ?? 0} />
        </div>

        <div className="h-[150px]">
          {curve.length > 1 ? (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={curve} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
                <XAxis dataKey="i" hide />
                <YAxis
                  orientation="right"
                  width={58}
                  tick={{ fontSize: 10, fontFamily: "Azeret Mono", fill: "#8A8A93" }}
                  tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`}
                  domain={["auto", "auto"]}
                />
                <Tooltip
                  contentStyle={{ background: "#121215", border: "1px solid #1F1F24", borderRadius: 2, fontFamily: "Azeret Mono", fontSize: 12 }}
                  formatter={(v) => [fmtUsd(v), "Equity"]}
                />
                <Line type="stepAfter" dataKey="equity" stroke="#00E676" strokeWidth={1.6} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-full grid place-items-center text-xs text-muted-foreground font-mono gap-1">
              <Activity className="h-4 w-4" />
              No closed paper trades yet. The live engine logs them as signals fire.
            </div>
          )}
        </div>

        <div>
          <div className="text-[10px] font-mono uppercase tracking-[0.1em] text-muted-foreground mb-1">Recent paper trades</div>
          <ScrollArea className="h-[120px]">
            {trades.length === 0 ? (
              <div className="text-xs text-muted-foreground font-mono py-4 text-center">—</div>
            ) : (
              <table className="w-full text-[11px] num">
                <tbody>
                  {trades.map((t) => (
                    <tr key={t.id} className="border-b border-border/30">
                      <td className="py-1.5 text-muted-foreground">{fmtTime(t.exit_time)}</td>
                      <td className="text-right">{fmtUsd(t.entry_price, 2)} → {fmtUsd(t.exit_price, 2)}</td>
                      <td className={`text-right ${pnlColor(t.return_pct)}`}>{fmtPct(t.return_pct)}</td>
                      <td className={`text-right pr-1 ${pnlColor(t.pnl)}`}>{fmtUsd(t.pnl)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </ScrollArea>
        </div>
      </div>
    </div>
  );
}
