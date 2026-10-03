import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from "recharts";
import { Dices, RefreshCw, Loader2 } from "lucide-react";
import { api, fmtUsd, pnlColor } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { InfoLabel } from "@/components/InfoLabel";

function Chip({ label, value, cls }) {
  return (
    <div className="border border-border/40 bg-background px-3 py-2 rounded-sm flex-1 min-w-[120px]">
      <div className="text-[9px] font-mono uppercase tracking-[0.1em] text-muted-foreground">{label}</div>
      <div className={`num text-base ${cls || ""}`}>{value}</div>
    </div>
  );
}

export function MiniSimulations({ strategy }) {
  const qc = useQueryClient();
  const sid = strategy?.id;
  const CAP = 20;
  const TRADES = 20;

  const { data, isFetching, isError } = useQuery({
    queryKey: ["simulate", sid],
    queryFn: async () =>
      (await api.get(`/strategies/${sid}/simulate?trades=${TRADES}&paths=400&capital=${CAP}`)).data,
    enabled: !!sid,
    retry: false,
    refetchOnWindowFocus: false,
  });

  const st = data?.stats;
  const tuning = data?.tuning;
  const paths = data?.sample_paths || [];
  const median = data?.median_path || [];
  const len = median.length || (paths[0]?.points?.length ?? 0);

  const rows = [];
  for (let i = 0; i < len; i++) {
    const row = { step: i };
    paths.forEach((p) => (row[p.id] = p.points[i]));
    row.median = median[i];
    rows.push(row);
  }

  return (
    <div className="border border-border/40 bg-card rounded-sm" data-testid="mini-simulations">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/40">
        <InfoLabel tip="A streak-aware 'what-if': we replay this crawler's past trades in realistic runs (keeping winning/losing streaks), 400 times, and tune the result to today's volatility and live Market Outlook. It shows how a $20 stake might grow or shrink over its next 20 trades — a rough preview, not a promise.">
          <span className="flex items-center gap-1.5"><Dices className="h-3.5 w-3.5" /> Mini Simulations</span>
        </InfoLabel>
        <Button
          size="sm"
          variant="outline"
          className="h-7 rounded-sm"
          disabled={!sid || isFetching}
          onClick={() => qc.invalidateQueries({ queryKey: ["simulate", sid] })}
          data-testid="run-sim-btn"
        >
          {isFetching ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5 mr-1" />}
          Run again
        </Button>
      </div>

      {isError || (!st && !isFetching) ? (
        <div className="h-[220px] grid place-items-center text-xs text-muted-foreground font-mono px-6 text-center">
          Test this crawler first — then TradeCrawler can simulate how $20 might do over its next {TRADES} trades.
        </div>
      ) : !st ? (
        <div className="h-[220px] grid place-items-center text-xs text-muted-foreground font-mono">
          <Loader2 className="h-4 w-4 animate-spin" />
        </div>
      ) : (
        <div className="p-4 space-y-3">
          <p className="text-[12px] text-muted-foreground">
            If you staked <span className="num text-foreground">${CAP}</span> on{" "}
            <span className="text-foreground">{strategy?.name}</span>'s next {TRADES} trades, here are 400 possible outcomes:
          </p>

          <div className="h-[150px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
                <XAxis dataKey="step" hide />
                <YAxis
                  orientation="right"
                  width={48}
                  tick={{ fontSize: 9, fontFamily: "Azeret Mono", fill: "hsl(var(--muted-foreground))" }}
                  tickFormatter={(v) => `$${v.toFixed(0)}`}
                  domain={["auto", "auto"]}
                />
                <Tooltip
                  contentStyle={{ background: "hsl(var(--popover))", border: "1px solid hsl(var(--border))", borderRadius: 2, fontFamily: "Azeret Mono", fontSize: 11 }}
                  formatter={(v) => [fmtUsd(v), ""]}
                  labelFormatter={(l) => `Trade ${l}`}
                />
                <ReferenceLine y={CAP} stroke="hsl(var(--muted-foreground))" strokeDasharray="3 3" strokeOpacity={0.6} />
                {paths.map((p, i) => (
                  <Line key={p.id} type="monotone" dataKey={p.id} stroke="hsl(var(--muted-foreground))" strokeWidth={1} strokeOpacity={0.22} dot={false} isAnimationActive={false} />
                ))}
                <Line type="monotone" dataKey="median" stroke="hsl(var(--primary))" strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="flex flex-wrap gap-2">
            <Chip label="Typical end" value={fmtUsd(st.median)} cls={pnlColor(st.median - CAP)} />
            <Chip label="Good case" value={fmtUsd(st.p90)} cls="text-buy" />
            <Chip label="Rough case" value={fmtUsd(st.p10)} cls="text-sell" />
            <Chip label="Chance of profit" value={`${st.prob_profit}%`} cls={st.prob_profit >= 50 ? "text-buy" : "text-sell"} />
            <Chip label="Worst dip on the way" value={`${st.median_max_drawdown_pct}%`} cls="text-sell" />
            <Chip label="Chance of losing half" value={`${st.prob_lose_half}%`} cls={st.prob_lose_half > 0 ? "text-sell" : "text-muted-foreground"} />
          </div>

          {st.expected_duration && (
            <p className="text-[12px] text-muted-foreground">
              These {TRADES} trades would typically play out over{" "}
              <span className="text-foreground num">{st.expected_duration}</span>.
            </p>
          )}

          {tuning?.applied ? (
            <p className="text-[11px] font-mono text-muted-foreground flex flex-wrap items-center gap-x-2" data-testid="sim-tuning">
              <span className="text-primary">Tuned to current market:</span>
              <span>{strategy?.symbol} outlook{" "}
                <span style={{ color: tuning.direction === "Up" ? "hsl(var(--buy))" : tuning.direction === "Down" ? "hsl(var(--sell))" : undefined }}>
                  {tuning.direction} ({tuning.confidence}%)
                </span>
              </span>
              {tuning.regime && <span>· volatility {tuning.regime.volatility}</span>}
              <span>· swing scale ×{tuning.vol_ratio}</span>
            </p>
          ) : tuning?.reason ? (
            <p className="text-[11px] font-mono text-muted-foreground" data-testid="sim-tuning">{tuning.reason}</p>
          ) : null}

          <p className="text-[10px] text-muted-foreground font-mono">
            Streak-aware replay of {st.sample_size} past trades · break-even line = ${CAP}. Rough preview only — not a prediction or advice.
          </p>
        </div>
      )}
    </div>
  );
}
