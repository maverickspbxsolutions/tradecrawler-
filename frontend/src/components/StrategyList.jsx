import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Play, Radar, Pencil, Trash2, Plus, CheckCircle2, FlaskConical, Loader2 } from "lucide-react";
import { api, fmtPct, pnlColor } from "@/lib/api";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { InfoLabel } from "@/components/InfoLabel";

function Metric({ label, tip, value, cls }) {
  return (
    <div>
      <InfoLabel tip={tip} className="text-[9px]">{label}</InfoLabel>
      <div className={`num text-sm ${cls || ""}`}>{value}</div>
    </div>
  );
}

function StrategyCard({ s, selected, onSelect, onEdit }) {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["strategies"] });
    qc.invalidateQueries({ queryKey: ["overview"] });
  };

  const patch = useMutation({
    mutationFn: (body) => api.put(`/strategies/${s.id}`, body),
    onSuccess: invalidate,
    onError: (e) => toast.error(e.response?.data?.detail || "Update failed"),
  });

  const backtest = useMutation({
    mutationFn: () => api.post(`/strategies/${s.id}/backtest`, { limit: 500 }),
    onSuccess: (r) => {
      invalidate();
      const d = r.data;
      toast[d.gate_passed ? "success" : "message"](
        d.gate_passed ? "Proven! This crawler will now email you alerts." : "Not good enough yet — it won't send alerts until it beats buy & hold.",
        { description: `Test result ${fmtPct(d.total_return_pct)} vs just holding ${fmtPct(d.benchmark_return_pct)}` }
      );
    },
    onError: (e) => toast.error(e.response?.data?.detail || "Re-test failed"),
  });

  const scan = useMutation({
    mutationFn: () => api.post(`/strategies/${s.id}/scan`),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["signals"] });
      invalidate();
      toast.message(r.data.signal ? `New ${r.data.signal.side} alert` : "Nothing to trade on the latest price bar");
    },
  });

  const del = useMutation({ mutationFn: () => api.delete(`/strategies/${s.id}`), onSuccess: invalidate });

  const bt = s.backtest;
  const proven = s.gate_passed;

  return (
    <div
      onClick={() => onSelect(s.id)}
      className={`border bg-card rounded-sm p-3 cursor-pointer transition-colors ${selected ? "border-primary/70" : "border-border/40 hover:border-border"}`}
      data-testid={`strategy-card-${s.id}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-medium truncate">{s.name}</div>
          <div className="num text-[11px] text-muted-foreground">{s.symbol.replace("USDT", "")} · {s.timeframe} chart</div>
        </div>
        <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
          <InfoLabel tip="When on, this crawler watches the market. Turn off to pause it.">Active</InfoLabel>
          <Switch checked={s.enabled} onCheckedChange={(v) => patch.mutate({ enabled: v })} data-testid={`toggle-enabled-${s.id}`} />
        </div>
      </div>

      <div className="grid grid-cols-4 gap-2 mt-3">
        <Metric label="Test profit" tip="How a $10,000 test grew on past data. Green = it made money." value={bt ? fmtPct(bt.total_return_pct) : "—"} cls={bt ? pnlColor(bt.total_return_pct) : ""} />
        <Metric label="Avg/trade" tip="Average result per trade. Positive means the edge pays off over many trades." value={bt ? fmtPct(bt.expectancy_pct) : "—"} cls={bt ? pnlColor(bt.expectancy_pct) : ""} />
        <Metric label="Worst drop" tip="Biggest fall from a high point during testing. Closer to 0 is safer." value={bt ? `${bt.max_drawdown_pct}%` : "—"} cls="text-sell" />
        <Metric label="Win:loss" tip="Average win size vs average loss size. Above 1 means wins beat losses." value={bt ? bt.risk_reward : "—"} />
      </div>

      <div className="flex items-center justify-between mt-3 pt-2.5 border-t border-border/40" onClick={(e) => e.stopPropagation()}>
        <span
          className={`text-[10px] font-mono uppercase tracking-wide flex items-center gap-1 px-2 py-0.5 rounded-sm border ${proven ? "text-buy border-buy/40 bg-buy/5" : "text-muted-foreground border-border"}`}
          data-testid={`gate-${s.id}`}
          title={proven ? "Proven on past data — it will email you alerts automatically." : "Still proving itself. It won't send alerts until it beats simply holding."}
        >
          {proven ? <CheckCircle2 className="h-3 w-3" /> : <FlaskConical className="h-3 w-3" />}
          {proven ? "Alerting" : "Testing…"}
        </span>
        <div className="flex items-center gap-0.5">
          <Button size="icon" variant="ghost" className="h-7 w-7 rounded-sm" onClick={() => backtest.mutate()} data-testid={`backtest-${s.id}`} title="Re-test on recent data">
            {backtest.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
          </Button>
          <Button size="icon" variant="ghost" className="h-7 w-7 rounded-sm" onClick={() => scan.mutate()} data-testid={`scan-${s.id}`} title="Check the market right now">
            {scan.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Radar className="h-3.5 w-3.5" />}
          </Button>
          <Button size="icon" variant="ghost" className="h-7 w-7 rounded-sm" onClick={() => onEdit(s)} data-testid={`edit-${s.id}`} title="Edit this crawler">
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button size="icon" variant="ghost" className="h-7 w-7 rounded-sm text-sell hover:text-sell" onClick={() => del.mutate()} data-testid={`delete-${s.id}`} title="Delete">
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}

export function StrategyList({ strategies, selectedId, onSelect, onEdit, onNew }) {
  return (
    <div className="border border-border/40 bg-card rounded-sm flex flex-col" data-testid="strategy-list">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/40">
        <InfoLabel tip="Each 'crawler' is a mini strategy with its own rules. Create as many as you like; the proven ones email you alerts.">Your Crawlers</InfoLabel>
        <Button size="sm" className="h-7 rounded-sm bg-primary text-primary-foreground hover:bg-primary/90" onClick={onNew} data-testid="new-strategy-btn">
          <Plus className="h-3.5 w-3.5 mr-1" /> New
        </Button>
      </div>
      <ScrollArea className="h-[566px]">
        <div className="p-2 space-y-2">
          {(strategies || []).map((s) => (
            <StrategyCard key={s.id} s={s} selected={s.id === selectedId} onSelect={onSelect} onEdit={onEdit} />
          ))}
          {(!strategies || strategies.length === 0) && (
            <div className="py-10 text-center text-xs text-muted-foreground font-mono">No crawlers yet. Tap “New”.</div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
