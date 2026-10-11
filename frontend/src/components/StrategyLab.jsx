import { useState, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { toast } from "sonner";
import { Brain, Upload, Wand2, Loader2, Save, Sparkles, FileText } from "lucide-react";

const ruleLabel = (r) => {
  const p = r.params || {};
  if (r.indicator === "rsi") return `RSI(${p.period || 14}) ${p.op || "below"} ${p.value}`;
  if (r.indicator === "ma_cross") return `${(p.ma_type || "ema").toUpperCase()} ${p.fast}/${p.slow} ${p.direction} cross`;
  if (r.indicator === "macd") return `MACD(${p.fast},${p.slow},${p.signal}) ${p.direction} cross`;
  if (r.indicator === "volume") return `Volume > ${p.multiplier || 1.5}× avg(${p.period || 20})`;
  return r.indicator;
};

function ParsedPreview({ parsed, onSave, saving }) {
  if (!parsed) return null;
  const b = parsed.bias || {};
  return (
    <div className="mt-3 rounded-lg border border-primary/30 bg-primary/5 p-3 space-y-2" data-testid="parsed-preview">
      <div className="flex items-center justify-between">
        <span className="font-mono text-sm font-semibold text-foreground">{parsed.name}</span>
        <span className="text-[10px] font-mono text-muted-foreground">
          {parsed.symbol} · {parsed.timeframe} · match {parsed.confidence}%
        </span>
      </div>
      {parsed.style_notes && <p className="text-xs text-muted-foreground">{parsed.style_notes}</p>}
      <div className="text-[11px] font-mono space-y-1">
        <div><span className="text-emerald-400">ENTER ({parsed.logic}):</span> {(parsed.entry_rules || []).map(ruleLabel).join(` ${parsed.logic} `) || "—"}</div>
        <div><span className="text-rose-400">EXIT:</span> {(parsed.exit_rules || []).map(ruleLabel).join(" OR ") || "engine-managed stop/target"}</div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {[["dir", b.direction], ["market", b.regime], ["entry", b.entry_style], ["risk", b.risk_appetite]].map(([k, v]) => (
          <span key={k} className="text-[10px] font-mono px-2 py-0.5 rounded bg-muted text-muted-foreground">{k}: {v}</span>
        ))}
      </div>
      <Button size="sm" onClick={onSave} disabled={saving} data-testid="save-strategy-btn" className="gap-2 w-full">
        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
        Save & teach the bot
      </Button>
    </div>
  );
}

export function StrategyLab() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const fileRef = useRef(null);
  const [text, setText] = useState("");
  const [parsed, setParsed] = useState(null);
  const [source, setSource] = useState("text");
  const [busy, setBusy] = useState(null);

  const rank = user?.license?.tier_rank || 0;
  const allowed = rank >= 3 || user?.license?.is_admin;

  const edge = useQuery({
    queryKey: ["platform-edge"],
    queryFn: async () => (await api.get("/strategies/platform-edge")).data,
    enabled: allowed,
  });

  if (!allowed) return null;

  const parseText = async () => {
    if (!text.trim()) return toast.error("Describe how you trade first.");
    setBusy("parse"); setParsed(null); setSource("text");
    try {
      const { data } = await api.post("/strategies/ai-parse", { text });
      setParsed(data.parsed);
      toast.success("Interpreted your strategy — review and save.");
    } catch (e) { toast.error(formatApiErrorDetail(e) || "Couldn't interpret that."); }
    finally { setBusy(null); }
  };

  const uploadFile = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setBusy("upload"); setParsed(null); setSource("file");
    try {
      const fd = new FormData();
      fd.append("file", f);
      const { data } = await api.post("/strategies/ai-upload", fd, { headers: { "Content-Type": "multipart/form-data" } });
      setParsed(data.parsed);
      toast.success(`Read ${f.name} — review and save.`);
    } catch (err) { toast.error(formatApiErrorDetail(err) || "Couldn't read that file."); }
    finally { setBusy(null); if (fileRef.current) fileRef.current.value = ""; }
  };

  const save = async () => {
    setBusy("save");
    try {
      await api.post("/strategies/ai-save", { parsed, source });
      setParsed(null); setText("");
      await qc.invalidateQueries({ queryKey: ["platform-edge"] });
      await qc.invalidateQueries({ queryKey: ["strategies"] });
      toast.success("Saved. The bot is now learning your style.");
    } catch (e) { toast.error(formatApiErrorDetail(e) || "Couldn't save."); }
    finally { setBusy(null); }
  };

  const pe = edge.data;
  return (
    <div className="rounded-xl border border-border/60 bg-card/40 p-4" data-testid="strategy-lab">
      <div className="flex items-center gap-2 mb-1">
        <Brain className="h-4 w-4 text-primary" />
        <h2 className="font-mono text-sm font-semibold uppercase tracking-[0.1em] text-foreground">Strategy Lab</h2>
        <span className="text-[10px] font-mono text-muted-foreground">advanced · teach the bot your edge</span>
      </div>
      <p className="text-xs text-muted-foreground mb-3">
        Upload how you trade and the bot learns your style — and folds it into a shared edge that sharpens signals for everyone. No code, demo-only.
      </p>

      {pe && (
        <div className="mb-3 rounded-lg border border-border/50 bg-background/50 p-3" data-testid="platform-edge">
          <div className="flex items-center gap-1.5 text-[11px] font-mono uppercase tracking-wide text-primary mb-1">
            <Sparkles className="h-3.5 w-3.5" /> What the bot has learned from everyone
          </div>
          <div className="text-xs text-muted-foreground">
            {pe.contributed_strategies > 0 ? (
              <>Learned from <strong className="text-foreground">{pe.contributed_strategies}</strong> strategies across <strong className="text-foreground">{pe.contributors}</strong> trader{pe.contributors === 1 ? "" : "s"}. Platform lean:{" "}
                <span className="text-foreground">{pe.preferred_direction || "—"}</span> ·{" "}
                <span className="text-foreground">{pe.preferred_regime || "—"}</span> markets ·{" "}
                <span className="text-foreground">{pe.preferred_entry_style || "—"}</span> entries ·{" "}
                <span className="text-foreground">{pe.risk_appetite || "—"}</span> risk.</>
            ) : "No strategies contributed yet — be the first to teach the bot."}
          </div>
        </div>
      )}

      <Tabs defaultValue="describe">
        <TabsList className="grid grid-cols-2 w-full">
          <TabsTrigger value="describe" data-testid="tab-describe"><Wand2 className="h-3.5 w-3.5 mr-1.5" />Describe it</TabsTrigger>
          <TabsTrigger value="upload" data-testid="tab-upload"><Upload className="h-3.5 w-3.5 mr-1.5" />Upload a file</TabsTrigger>
        </TabsList>

        <TabsContent value="describe" className="mt-3">
          <Textarea
            data-testid="strategy-text-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Tell the bot how you trade… e.g. 'I buy pullbacks to the 20 EMA on gold when the trend is up and RSI dips under 40, take profit when RSI tops 70. Trend continuation, medium risk.'"
            className="min-h-[120px] font-mono text-xs"
          />
          <Button size="sm" onClick={parseText} disabled={busy === "parse"} data-testid="interpret-btn" className="gap-2 mt-2">
            {busy === "parse" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
            Interpret with AI
          </Button>
        </TabsContent>

        <TabsContent value="upload" className="mt-3">
          <div className="rounded-lg border border-dashed border-border/60 p-5 text-center">
            <FileText className="h-6 w-6 mx-auto text-muted-foreground mb-2" />
            <p className="text-xs text-muted-foreground mb-3">
              Upload a strategy write-up (PDF/TXT) or a CSV of your past trades. The AI reads it and the bot learns what works for you.
            </p>
            <input ref={fileRef} type="file" accept=".pdf,.csv,.txt" onChange={uploadFile} className="hidden" data-testid="strategy-file-input" />
            <Button size="sm" onClick={() => fileRef.current?.click()} disabled={busy === "upload"} data-testid="choose-file-btn" className="gap-2">
              {busy === "upload" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
              Choose file
            </Button>
          </div>
        </TabsContent>
      </Tabs>

      <ParsedPreview parsed={parsed} onSave={save} saving={busy === "save"} />
    </div>
  );
}
