import { useState, useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, X } from "lucide-react";
import { api } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "MATICUSDT"];
const TFS = ["1m", "5m", "15m", "1h", "4h", "1d"];
const INDICATORS = [
  { value: "rsi", label: "RSI" },
  { value: "ma_cross", label: "MA Crossover" },
  { value: "macd", label: "MACD" },
  { value: "volume", label: "Volume" },
];

const DEFAULTS = {
  rsi: { period: 14, op: "below", value: 30 },
  ma_cross: { fast: 9, slow: 21, ma_type: "ema", direction: "bull" },
  macd: { fast: 12, slow: 26, signal: 9, direction: "bull" },
  volume: { period: 20, multiplier: 1.5 },
};

function Num({ label, value, onChange, step = 1 }) {
  return (
    <div className="flex-1">
      <Label className="text-[9px] font-mono uppercase text-muted-foreground">{label}</Label>
      <Input
        type="number"
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="h-8 rounded-sm num text-xs mt-0.5"
      />
    </div>
  );
}

function Pick({ label, value, onChange, options }) {
  return (
    <div className="flex-1">
      <Label className="text-[9px] font-mono uppercase text-muted-foreground">{label}</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="h-8 rounded-sm text-xs mt-0.5 font-mono">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value} className="text-xs font-mono">
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function RuleRow({ rule, onChange, onRemove }) {
  const p = rule.params;
  const set = (k, v) => onChange({ ...rule, params: { ...p, [k]: v } });
  const setInd = (ind) => onChange({ indicator: ind, params: { ...DEFAULTS[ind] } });

  return (
    <div className="border border-border/50 bg-background rounded-sm p-2 space-y-2">
      <div className="flex items-center gap-2">
        <Pick value={rule.indicator} onChange={setInd} options={INDICATORS} label="Indicator" />
        <Button size="icon" variant="ghost" className="h-7 w-7 rounded-sm text-sell mt-4" onClick={onRemove}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      <div className="flex gap-2">
        {rule.indicator === "rsi" && (
          <>
            <Num label="Period" value={p.period} onChange={(v) => set("period", v)} />
            <Pick label="Condition" value={p.op} onChange={(v) => set("op", v)} options={[{ value: "below", label: "Below" }, { value: "above", label: "Above" }]} />
            <Num label="Value" value={p.value} onChange={(v) => set("value", v)} />
          </>
        )}
        {rule.indicator === "ma_cross" && (
          <>
            <Pick label="Type" value={p.ma_type} onChange={(v) => set("ma_type", v)} options={[{ value: "ema", label: "EMA" }, { value: "sma", label: "SMA" }]} />
            <Num label="Fast" value={p.fast} onChange={(v) => set("fast", v)} />
            <Num label="Slow" value={p.slow} onChange={(v) => set("slow", v)} />
            <Pick label="Cross" value={p.direction} onChange={(v) => set("direction", v)} options={[{ value: "bull", label: "Golden ↑" }, { value: "bear", label: "Death ↓" }]} />
          </>
        )}
        {rule.indicator === "macd" && (
          <>
            <Num label="Fast" value={p.fast} onChange={(v) => set("fast", v)} />
            <Num label="Slow" value={p.slow} onChange={(v) => set("slow", v)} />
            <Num label="Signal" value={p.signal} onChange={(v) => set("signal", v)} />
            <Pick label="Cross" value={p.direction} onChange={(v) => set("direction", v)} options={[{ value: "bull", label: "Bullish ↑" }, { value: "bear", label: "Bearish ↓" }]} />
          </>
        )}
        {rule.indicator === "volume" && (
          <>
            <Num label="Avg Period" value={p.period} onChange={(v) => set("period", v)} />
            <Num label="Multiplier" value={p.multiplier} step={0.1} onChange={(v) => set("multiplier", v)} />
          </>
        )}
      </div>
    </div>
  );
}

function RuleSection({ title, rules, setRules, kind }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <Label className="text-[10px] font-mono uppercase tracking-[0.1em] text-muted-foreground">{title}</Label>
        <Button
          size="sm"
          variant="outline"
          className="h-6 rounded-sm text-[10px]"
          onClick={() => setRules([...rules, { indicator: "rsi", params: { ...DEFAULTS.rsi } }])}
          data-testid={`add-${kind}-rule`}
        >
          <Plus className="h-3 w-3 mr-1" /> Add
        </Button>
      </div>
      <div className="space-y-2">
        {rules.map((r, i) => (
          <RuleRow
            key={i}
            rule={r}
            onChange={(nr) => setRules(rules.map((x, j) => (j === i ? nr : x)))}
            onRemove={() => setRules(rules.filter((_, j) => j !== i))}
          />
        ))}
        {rules.length === 0 && <div className="text-[11px] text-muted-foreground font-mono py-1">No rules.</div>}
      </div>
    </div>
  );
}

export function StrategyFormDialog({ open, onOpenChange, strategy }) {
  const qc = useQueryClient();
  const editing = !!strategy;
  const [form, setForm] = useState(null);

  useEffect(() => {
    if (!open) return;
    setForm(
      strategy
        ? {
            name: strategy.name,
            symbol: strategy.symbol,
            timeframe: strategy.timeframe,
            logic: strategy.logic,
            fees_pct: strategy.fees_pct,
            slippage_pct: strategy.slippage_pct,
            entry_rules: JSON.parse(JSON.stringify(strategy.entry_rules || [])),
            exit_rules: JSON.parse(JSON.stringify(strategy.exit_rules || [])),
          }
        : {
            name: "",
            symbol: "BTCUSDT",
            timeframe: "1h",
            logic: "AND",
            fees_pct: 0.1,
            slippage_pct: 0.05,
            entry_rules: [{ indicator: "rsi", params: { ...DEFAULTS.rsi } }],
            exit_rules: [{ indicator: "rsi", params: { period: 14, op: "above", value: 70 } }],
          }
    );
  }, [open, strategy]);

  const save = useMutation({
    mutationFn: (body) => (editing ? api.put(`/strategies/${strategy.id}`, body) : api.post("/strategies", body)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["strategies"] });
      qc.invalidateQueries({ queryKey: ["signals"] });
      toast.success(editing ? "Strategy updated" : "Strategy created");
      onOpenChange(false);
    },
    onError: (e) => toast.error(e.response?.data?.detail || "Save failed"),
  });

  if (!form) return null;
  const set = (k, v) => setForm({ ...form, [k]: v });

  const submit = () => {
    if (!form.name.trim()) return toast.error("Name required");
    if (form.entry_rules.length === 0) return toast.error("Add at least one entry rule");
    save.mutate(form);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl rounded-sm max-h-[88vh] overflow-y-auto" data-testid="strategy-form">
        <DialogHeader>
          <DialogTitle className="text-base">{editing ? "Edit Strategy" : "New Strategy"}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <Label className="text-[10px] font-mono uppercase text-muted-foreground">Name</Label>
              <Input value={form.name} onChange={(e) => set("name", e.target.value)} className="h-8 rounded-sm mt-0.5" placeholder="e.g. RSI Mean Reversion" data-testid="strategy-name-input" />
            </div>
            <Pick label="Symbol" value={form.symbol} onChange={(v) => set("symbol", v)} options={SYMBOLS.map((s) => ({ value: s, label: s }))} />
            <Pick label="Timeframe" value={form.timeframe} onChange={(v) => set("timeframe", v)} options={TFS.map((t) => ({ value: t, label: t }))} />
            <Pick label="Rule Logic" value={form.logic} onChange={(v) => set("logic", v)} options={[{ value: "AND", label: "AND (all)" }, { value: "OR", label: "OR (any)" }]} />
            <div className="flex gap-3">
              <Num label="Fees %" value={form.fees_pct} step={0.01} onChange={(v) => set("fees_pct", v)} />
              <Num label="Slippage %" value={form.slippage_pct} step={0.01} onChange={(v) => set("slippage_pct", v)} />
            </div>
          </div>

          <RuleSection title="Entry (Buy) Rules" rules={form.entry_rules} setRules={(r) => set("entry_rules", r)} kind="entry" />
          <RuleSection title="Exit (Sell) Rules" rules={form.exit_rules} setRules={(r) => set("exit_rules", r)} kind="exit" />
        </div>

        <DialogFooter>
          <Button variant="ghost" className="rounded-sm" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button className="rounded-sm bg-primary text-primary-foreground hover:bg-primary/90" onClick={submit} disabled={save.isPending} data-testid="save-strategy-btn">
            {editing ? "Save Changes" : "Create Strategy"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
