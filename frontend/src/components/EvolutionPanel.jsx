import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from "recharts";
import { toast } from "sonner";
import { Dna, Loader2, TrendingUp, TrendingDown, Minus, Activity, CheckCircle2, CircleSlash } from "lucide-react";
import { api, fmtPct, pnlColor, fmtTime } from "@/lib/api";
import { useChartTheme } from "@/lib/useChartTheme";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";

function RegimeBadge({ regime }) {
  if (!regime) return null;
  const t = regime.trend;
  const Icon = t === "up" ? TrendingUp : t === "down" ? TrendingDown : Minus;
  const cls = t === "up" ? "text-buy border-buy/40" : t === "down" ? "text-sell border-sell/40" : "text-muted-foreground border-border";
  return (
    <div className="flex items-center gap-2 flex-wrap" data-testid="regime-badges">
      <span className={`text-[10px] font-mono uppercase px-2 py-0.5 rounded-sm border flex items-center gap-1 ${cls}`}>
        <Icon className="h-3 w-3" /> {t}
      </span>
      <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded-sm border border-border text-muted-foreground flex items-center gap-1">
        <Activity className="h-3 w-3" /> vol {regime.volatility}
      </span>
      <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded-sm border border-border text-muted-foreground">
        strength {regime.strength}
      </span>
    </div>
  );
}

function diffParams(oldP, newP) {
  const out = [];
  if (!oldP || !newP) return out;
  ["entry", "exit"].forEach((scope) => {
    (oldP[scope] || []).forEach((o, i) => {
      const nw = (newP[scope] || [])[i] || {};
      Object.keys(o).forEach((k) => {
        if (typeof o[k] === "number" && o[k] !== nw[k]) {
          out.push({ scope, k, from: o[k], to: nw[k] });
        }
      });
    });
  });
  return out;
}

export function EvolutionPanel({ strategy }) {
  const theme = useChartTheme();
  const qc = useQueryClient();
  const sid = strategy?.id;

  const { data } = useQuery({
    queryKey: ["evolution", sid],
    queryFn: async () => (await api.get(`/strategies/${sid}/evolution`)).data,
    enabled: !!sid,
    refetchInterval: 60000,
  });

  const evolveNow = useMutation({
    mutationFn: () => api.post(`/strategies/${sid}/evolve`),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["evolution", sid] });
      qc.invalidateQueries({ queryKey: ["strategies"] });
      qc.invalidateQueries({ queryKey: ["backtest", sid] });
      const d = r.data;
      toast[d.applied ? "success" : "message"](
        d.applied ? "Upgraded! Found better settings and switched to them" : d.improved ? "Found better settings — turn on Auto-improve to use them" : "Current settings are still the best",
        { description: `Out-of-sample score ${d.current_oos_fitness} → ${d.candidate_oos_fitness}` }
      );
    },
    onError: (e) => toast.error(e.response?.data?.detail || "Evolution failed"),
  });

  const toggleAuto = useMutation({
    mutationFn: (v) => api.put(`/strategies/${sid}`, { auto_evolve: v }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["strategies"] }),
  });

  const latest = data?.latest;
  const gens = (latest?.generations || []).map((g) => ({ gen: g.gen, Best: g.best, Avg: g.avg }));
  const changes = latest ? diffParams(latest.old_params, latest.new_params) : [];

  return (
    <div className="border border-border/40 bg-card rounded-sm" data-testid="evolution-panel">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/40 flex-wrap gap-2">
        <div className="text-xs font-mono uppercase tracking-[0.15em] text-muted-foreground flex items-center gap-2">
          <Dna className="h-3.5 w-3.5 text-primary" /> Self-improving · {strategy?.name || "—"}
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span className="text-[9px] font-mono uppercase text-muted-foreground">Auto-improve</span>
            <Switch
              checked={strategy?.auto_evolve ?? true}
              onCheckedChange={(v) => toggleAuto.mutate(v)}
              data-testid="toggle-auto-evolve"
            />
          </div>
          <Button
            size="sm"
            className="h-7 rounded-sm bg-primary text-primary-foreground hover:bg-primary/90"
            disabled={!sid || evolveNow.isPending}
            onClick={() => evolveNow.mutate()}
            data-testid="evolve-now-btn"
          >
            {evolveNow.isPending ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Dna className="h-3.5 w-3.5 mr-1" />}
            Improve Now
          </Button>
        </div>
      </div>

      {!latest ? (
        <div className="h-[300px] grid place-items-center text-xs text-muted-foreground font-mono px-6 text-center">
          {evolveNow.isPending ? "Trying out new settings and keeping what works…" : "Tap “Improve now” and TradeCrawler will test lots of new settings, then keep the ones that work better on data it hasn't seen."}
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 p-4">
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-[10px] font-mono uppercase tracking-[0.1em] text-muted-foreground">Current market regime</div>
              <span className="text-[10px] font-mono text-muted-foreground">{fmtTime(latest.ran_at)}</span>
            </div>
            <RegimeBadge regime={latest.regime} />

            <div className="h-[170px] mt-2">
              <div className="text-[10px] font-mono uppercase tracking-[0.1em] text-muted-foreground mb-1">Fitness per generation</div>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={gens} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
                  <XAxis dataKey="gen" tick={{ fontSize: 9, fontFamily: "Azeret Mono", fill: theme.axis }} />
                  <YAxis width={40} tick={{ fontSize: 9, fontFamily: "Azeret Mono", fill: theme.axis }} domain={["auto", "auto"]} />
                  <Tooltip contentStyle={{ background: theme.tooltipBg, border: `1px solid ${theme.tooltipBorder}`, color: theme.tooltipText, borderRadius: 2, fontFamily: "Azeret Mono", fontSize: 11 }} />
                  <Legend wrapperStyle={{ fontSize: 9, fontFamily: "Azeret Mono" }} />
                  <Line type="monotone" dataKey="Best" stroke={theme.accent} strokeWidth={1.6} dot={false} />
                  <Line type="monotone" dataKey="Avg" stroke={theme.muted} strokeWidth={1.2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="space-y-3">
            <div
              className={`flex items-center gap-2 text-xs font-mono px-3 py-2 rounded-sm border ${
                latest.applied ? "border-buy/40 text-buy bg-buy/5" : "border-border text-muted-foreground"
              }`}
              data-testid="evolution-verdict"
            >
              {latest.applied ? <CheckCircle2 className="h-3.5 w-3.5" /> : <CircleSlash className="h-3.5 w-3.5" />}
              {latest.applied
                ? "Upgraded — found better settings and switched to them"
                : latest.improved
                ? "Found better settings — turn on Auto-improve to use them"
                : "Current settings are still the best"}
            </div>

            <div className="grid grid-cols-3 gap-2">
              <div className="border border-border/40 bg-background px-3 py-2 rounded-sm">
                <div className="text-[9px] font-mono uppercase text-muted-foreground">OOS fitness now</div>
                <div className="num text-base">{latest.current_oos_fitness}</div>
              </div>
              <div className="border border-border/40 bg-background px-3 py-2 rounded-sm">
                <div className="text-[9px] font-mono uppercase text-muted-foreground">OOS candidate</div>
                <div className="num text-base text-primary">{latest.candidate_oos_fitness}</div>
              </div>
              <div className="border border-border/40 bg-background px-3 py-2 rounded-sm">
                <div className="text-[9px] font-mono uppercase text-muted-foreground">Δ improvement</div>
                <div className={`num text-base ${pnlColor(latest.improvement)}`}>{latest.improvement > 0 ? "+" : ""}{latest.improvement}</div>
              </div>
            </div>

            <div>
              <div className="text-[10px] font-mono uppercase tracking-[0.1em] text-muted-foreground mb-1">
                Candidate out-of-sample · {latest.candidate_oos?.num_trades ?? 0} trades
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1 num text-[11px]">
                <span className={pnlColor(latest.candidate_oos?.total_return_pct)}>
                  return {fmtPct(latest.candidate_oos?.total_return_pct)}
                </span>
                <span className="text-muted-foreground">vs B&H {fmtPct(latest.candidate_oos?.benchmark_return_pct)}</span>
                <span className={pnlColor(latest.candidate_oos?.expectancy_pct)}>exp {fmtPct(latest.candidate_oos?.expectancy_pct)}</span>
                <span className="text-sell">dd {latest.candidate_oos?.max_drawdown_pct}%</span>
              </div>
            </div>

            {changes.length > 0 && (
              <div>
                <div className="text-[10px] font-mono uppercase tracking-[0.1em] text-muted-foreground mb-1">Parameter changes</div>
                <div className="flex flex-wrap gap-1.5">
                  {changes.slice(0, 8).map((c, i) => (
                    <span key={i} className="num text-[10px] px-1.5 py-0.5 rounded-sm border border-border/60 text-muted-foreground">
                      {c.scope}.{c.k}: <span className="text-sell">{c.from}</span>→<span className="text-buy">{c.to}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div className="text-[10px] text-muted-foreground font-mono leading-relaxed pt-1">
              TradeCrawler tests new settings on old data, then double-checks them on data it hasn't seen before keeping them. It only upgrades when the new settings truly do better. Runs automatically every day.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
