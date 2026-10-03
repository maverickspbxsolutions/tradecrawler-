import { useQuery } from "@tanstack/react-query";
import { api, fmtUsd, pnlColor } from "@/lib/api";
import { InfoLabel } from "@/components/InfoLabel";

function Stat({ label, tip, value, sub, valueClass, testid }) {
  return (
    <div className="flex-1 min-w-[150px] border border-border/40 bg-card px-4 py-3 rounded-sm" data-testid={testid}>
      {tip ? <InfoLabel tip={tip}>{label}</InfoLabel> : (
        <div className="text-[10px] font-mono uppercase tracking-[0.15em] text-muted-foreground">{label}</div>
      )}
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
        label="Crawlers alerting"
        tip="How many of your crawlers have proven they work and are now emailing you live trade alerts."
        value={`${o.num_live ?? 0}/${o.num_strategies ?? 0}`}
        sub={`${o.num_enabled ?? 0} watching the market`}
        valueClass="text-primary"
      />
      <Stat
        testid="stat-paper-pnl"
        label="Practice profit"
        tip="Pretend-money result if you'd followed every alert. No real money is used — this just shows how the crawlers are doing."
        value={fmtUsd(o.total_paper_pnl ?? 0)}
        sub={`${o.total_paper_trades ?? 0} practice trades`}
        valueClass={pnlColor(o.total_paper_pnl ?? 0)}
      />
      <Stat
        testid="stat-winrate"
        label="Win rate"
        tip="Share of practice trades that made money. Useful, but not the whole story — a few big wins matter more than many tiny ones."
        value={o.total_paper_trades ? `${o.win_rate}%` : "—"}
        sub="of practice trades won"
      />
      <Stat
        testid="stat-signals"
        label="Alerts today"
        tip="Buy/sell trade alerts TradeCrawler has found in the last 24 hours."
        value={o.signals_today ?? 0}
        sub={`${o.alerts_today ?? 0} emailed to you`}
      />
      <Stat
        testid="stat-engine"
        label="Checking every"
        tip="TradeCrawler re-checks the live market this often, 24/7, looking for a good trade."
        value={`${o.poll_seconds ?? 30}s`}
        sub={o.data_source ? `live data · ${o.data_source}` : "—"}
      />
    </div>
  );
}
