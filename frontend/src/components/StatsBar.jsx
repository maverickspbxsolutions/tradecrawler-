import { useQuery } from "@tanstack/react-query";
import { api, fmtUsd, fmtPct, pnlColor } from "@/lib/api";

function Stat({ label, value, sub, valueClass, testid }) {
  return (
    <div className="flex-1 min-w-[140px] border border-border/40 bg-card px-4 py-3 rounded-sm" data-testid={testid}>
      <div className="text-[10px] font-mono uppercase tracking-[0.15em] text-muted-foreground">{label}</div>
      <div className={`num text-2xl mt-1 ${valueClass || ""}`}>{value}</div>
      {sub && <div className="text-[11px] text-muted-foreground mt-0.5">{sub}</div>}
    </div>
  );
}

export function StatsBar() {
  const { data } = useQuery({
    queryKey: ["overview"],
    queryFn: async () => (await api.get("/overview")).data,
    refetchInterval: 15000,
  });

  const o = data || {};
  return (
    <div className="flex flex-wrap gap-1 md:gap-3 mb-3">
      <Stat
        testid="stat-live"
        label="Live Strategies"
        value={`${o.num_live ?? 0}/${o.num_strategies ?? 0}`}
        sub={`${o.num_enabled ?? 0} scanning`}
        valueClass="text-primary"
      />
      <Stat
        testid="stat-paper-pnl"
        label="Paper P&L"
        value={fmtUsd(o.total_paper_pnl ?? 0)}
        sub={`${o.total_paper_trades ?? 0} closed trades`}
        valueClass={pnlColor(o.total_paper_pnl ?? 0)}
      />
      <Stat
        testid="stat-winrate"
        label="Win Rate"
        value={o.total_paper_trades ? `${o.win_rate}%` : "—"}
        sub="secondary metric"
      />
      <Stat testid="stat-signals" label="Signals (24h)" value={o.signals_today ?? 0} sub={`${o.alerts_today ?? 0} alerts sent`} />
      <Stat
        testid="stat-engine"
        label="Poll Interval"
        value={`${o.poll_seconds ?? 60}s`}
        sub={o.data_source ? `via ${o.data_source}` : "—"}
      />
    </div>
  );
}
