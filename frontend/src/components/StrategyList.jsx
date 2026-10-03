import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Play,
  Radar,
  Pencil,
  Trash2,
  Plus,
  ShieldCheck,
  ShieldAlert,
  Loader2,
} from "lucide-react";
import { api, fmtPct, pnlColor } from "@/lib/api";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";

function Metric({ label, value, cls }) {
  return (
    <div>
      <div className="text-[9px] font-mono uppercase tracking-[0.1em] text-muted-foreground">{label}</div>
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
      toast[d.beats_benchmark ? "success" : "message"](
        `Backtest: ${fmtPct(d.total_return_pct)} vs B&H ${fmtPct(d.benchmark_return_pct)}`,
        { description: d.gate_passed ? "Gate PASSED — eligible to go live" : "Gate not met" }
      );
    },
    onError: (e) => toast.error(e.response?.data?.detail || "Backtest failed"),
  });

  const scan = useMutation({
    mutationFn: () => api.post(`/strategies/${s.id}/scan`),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["signals"] });
      invalidate();
      toast.message(r.data.signal ? `New ${r.data.signal.side} signal` : "No new signal on the latest closed candle");
    },
  });

  const del = useMutation({
    mutationFn: () => api.delete(`/strategies/${s.id}`),
    onSuccess: invalidate,
  });

  const bt = s.backtest;
  const gate = s.gate_passed;

  return (
    <div
      onClick={() => onSelect(s.id)}
      className={`border bg-card rounded-sm p-3 cursor-pointer transition-colors ${
        selected ? "border-primary/70" : "border-border/40 hover:border-border"
      }`}
      data-testid={`strategy-card-${s.id}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-medium truncate">{s.name}</div>
          <div className="num text-[11px] text-muted-foreground">
            {s.symbol} · {s.timeframe}
          </div>
        </div>
        <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
          <span className="text-[9px] font-mono uppercase text-muted-foreground">On</span>
          <Switch
            checked={s.enabled}
            onCheckedChange={(v) => patch.mutate({ enabled: v })}
            data-testid={`toggle-enabled-${s.id}`}
          />
        </div>
      </div>

      <div className="grid grid-cols-4 gap-2 mt-3">
        <Metric label="Return" value={bt ? fmtPct(bt.total_return_pct) : "—"} cls={bt ? pnlColor(bt.total_return_pct) : ""} />
        <Metric label="Expectancy" value={bt ? `${fmtPct(bt.expectancy_pct)}` : "—"} cls={bt ? pnlColor(bt.expectancy_pct) : ""} />
        <Metric label="Max DD" value={bt ? `${bt.max_drawdown_pct}%` : "—"} cls="text-sell" />
        <Metric label="R:R" value={bt ? bt.risk_reward : "—"} />
      </div>

      <div className="flex items-center justify-between mt-3 pt-2.5 border-t border-border/40" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2">
          <span
            className={`text-[9px] font-mono uppercase tracking-wide flex items-center gap-1 ${
              gate ? "text-buy" : "text-muted-foreground"
            }`}
            data-testid={`gate-${s.id}`}
          >
            {gate ? <ShieldCheck className="h-3 w-3" /> : <ShieldAlert className="h-3 w-3" />}
            {gate ? "gate passed" : "gated"}
          </span>
          <span className="text-[9px] font-mono uppercase text-muted-foreground">Live</span>
          <Switch
            checked={s.live}
            disabled={!gate}
            onCheckedChange={(v) => patch.mutate({ live: v })}
            data-testid={`toggle-live-${s.id}`}
          />
        </div>
        <div className="flex items-center gap-0.5">
          <Button size="icon" variant="ghost" className="h-7 w-7 rounded-sm" onClick={() => backtest.mutate()} data-testid={`backtest-${s.id}`} title="Backtest">
            {backtest.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
          </Button>
          <Button size="icon" variant="ghost" className="h-7 w-7 rounded-sm" onClick={() => scan.mutate()} data-testid={`scan-${s.id}`} title="Scan now">
            {scan.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Radar className="h-3.5 w-3.5" />}
          </Button>
          <Button size="icon" variant="ghost" className="h-7 w-7 rounded-sm" onClick={() => onEdit(s)} data-testid={`edit-${s.id}`} title="Edit">
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
        <div className="text-xs font-mono uppercase tracking-[0.15em] text-muted-foreground">Strategies</div>
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
            <div className="py-10 text-center text-xs text-muted-foreground font-mono">No strategies. Create one.</div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
