import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ShieldCheck, ShieldAlert, Check, X, TrendingUp, TrendingDown,
  Loader2, Gauge, ScrollText, CircleDollarSign, Target,
} from "lucide-react";
import { api, fmtUsd, fmtNum, pnlColor, fmtTime, formatApiErrorDetail } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";

function Stat({ label, value, cls }) {
  return (
    <div className="border border-border/40 bg-background px-3 py-2 rounded-sm">
      <div className="text-[9px] font-mono uppercase tracking-[0.1em] text-muted-foreground">{label}</div>
      <div className={`num text-base ${cls || ""}`}>{value}</div>
    </div>
  );
}

export function TradePanel() {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(null);

  const { data: account } = useQuery({
    queryKey: ["trade-account"],
    queryFn: async () => (await api.get("/trades/account")).data,
    refetchInterval: 15000,
  });
  const { data: symbols = [] } = useQuery({
    queryKey: ["trade-active-symbols"],
    queryFn: async () => (await api.get("/trades/active-symbols")).data,
    refetchInterval: 30000,
  });
  const { data: scanStatus = [] } = useQuery({
    queryKey: ["trade-scan-status"],
    queryFn: async () => (await api.get("/trades/scan-status")).data,
    refetchInterval: 20000,
  });
  const { data: pnl } = useQuery({
    queryKey: ["trade-pnl"],
    queryFn: async () => (await api.get("/trades/pnl")).data,
    refetchInterval: 10000,
  });
  const { data: proposals = [] } = useQuery({
    queryKey: ["trade-proposals"],
    queryFn: async () => (await api.get("/trades/proposals")).data,
    refetchInterval: 8000,
  });
  const { data: positions = [] } = useQuery({
    queryKey: ["trade-positions"],
    queryFn: async () => (await api.get("/trades/positions")).data,
    refetchInterval: 8000,
  });
  const { data: log = [] } = useQuery({
    queryKey: ["trade-log"],
    queryFn: async () => (await api.get("/trades/log")).data,
    refetchInterval: 15000,
  });

  const refresh = () =>
    ["trade-account", "trade-pnl", "trade-proposals", "trade-positions", "trade-log", "trade-scan-status"].forEach((k) =>
      qc.invalidateQueries({ queryKey: [k] })
    );

  const err = (e, fb) => toast.error(formatApiErrorDetail(e?.response?.data?.detail) || fb);

  const propose = async (symbol) => {
    setBusy(`propose-${symbol}`);
    try {
      const { data } = await api.post("/trades/propose", { symbol });
      toast.success(`${data.side} proposal ready for review — ${symbol}`);
      refresh();
    } catch (e) {
      err(e, "Could not build a proposal");
    } finally {
      setBusy(null);
    }
  };

  const decide = async (pid, action) => {
    setBusy(`${action}-${pid}`);
    try {
      await api.post(`/trades/proposals/${pid}/${action}`);
      toast.success(action === "approve" ? "Order placed on demo account" : "Proposal rejected");
      refresh();
    } catch (e) {
      err(e, "Action failed");
    } finally {
      setBusy(null);
    }
  };

  const closePos = async (id) => {
    setBusy(`close-${id}`);
    try {
      await api.post(`/trades/positions/${id}/close`);
      toast.success("Position closed");
      refresh();
    } catch (e) {
      err(e, "Close failed");
    } finally {
      setBusy(null);
    }
  };

  const pending = proposals.filter((p) => p.status === "pending");
  const history = proposals.filter((p) => p.status !== "pending").slice(0, 8);
  const limit = pnl?.limit ?? 50;
  const used = pnl?.loss_used ?? 0;
  const pctUsed = Math.min(100, (used / limit) * 100);
  const overLimit = pnl?.over_limit;
  const enforced = pnl?.enforced;
  const blocked = enforced && overLimit; // only blocks when enforced (live)

  return (
    <div className="border border-border/40 bg-card rounded-sm" data-testid="trade-panel">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/40">
        <div className="text-xs font-mono uppercase tracking-[0.15em] text-muted-foreground flex items-center gap-2">
          <CircleDollarSign className="h-3.5 w-3.5" /> Demo auto-trading · approve each trade
        </div>
        <div className="flex items-center gap-2">
          {account?.is_demo ? (
            <Badge variant="outline" className="text-[10px] font-mono border-buy/50 text-buy gap-1" data-testid="trade-demo-badge">
              <ShieldCheck className="h-3 w-3" /> DEMO
            </Badge>
          ) : (
            <Badge variant="outline" className="text-[10px] font-mono border-destructive/50 text-destructive gap-1">
              <ShieldAlert className="h-3 w-3" /> {account?.available ? "NOT DEMO" : "OFFLINE"}
            </Badge>
          )}
        </div>
      </div>

      <div className="p-4 space-y-4">
        {/* account + daily pnl / kill switch */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <Stat label="Balance" value={account?.available ? fmtUsd(account.balance) : "—"} />
          <Stat label="Equity" value={account?.available ? fmtUsd(account.equity) : "—"} />
          <Stat label="Realized today" value={fmtUsd(pnl?.realized)} cls={pnlColor(pnl?.realized)} />
          <Stat label="Floating" value={fmtUsd(pnl?.floating)} cls={pnlColor(pnl?.floating)} />
        </div>

        {/* 2x balance growth goal */}
        {account?.available && account?.growth && (
          <div className="border border-primary/40 bg-primary/5 rounded-sm p-3" data-testid="growth-goal">
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[10px] font-mono uppercase tracking-[0.1em] text-muted-foreground flex items-center gap-1.5">
                <Target className="h-3.5 w-3.5" /> Goal · double the balance
              </span>
              <span className="num text-sm text-primary">
                {fmtUsd(account.equity)} / {fmtUsd(account.growth.target_balance)}
              </span>
            </div>
            <Progress value={account.growth.growth_pct} className="h-2 [&>div]:bg-primary" />
            <div className="mt-1.5 text-[10px] font-mono text-muted-foreground">
              {account.growth.goal_reached
                ? "🎯 2× goal reached — the bot keeps compounding from here."
                : `${account.growth.growth_pct}% of the way from ${fmtUsd(account.growth.start_balance)} to the ${fmtUsd(account.growth.target_balance)} goal. SL/TP are set from each trade's prediction to grow the balance (no guarantees).`}
            </div>
          </div>
        )}

        <div className="border border-border/40 bg-background rounded-sm p-3" data-testid="kill-switch">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[10px] font-mono uppercase tracking-[0.1em] text-muted-foreground flex items-center gap-1.5">
              <Gauge className="h-3.5 w-3.5" /> Daily P&L {enforced ? "· loss limit" : "· tracking only (demo)"}
            </span>
            <span className={`num text-sm ${overLimit ? "text-destructive" : ""}`}>
              {fmtUsd(used)} / {fmtUsd(limit)}
            </span>
          </div>
          <Progress value={pctUsed} className={`h-2 ${overLimit ? "[&>div]:bg-destructive" : "[&>div]:bg-warn"}`} />
          {blocked ? (
            <div className="mt-2 text-[11px] font-mono text-destructive flex items-center gap-1.5" data-testid="kill-switch-active">
              <ShieldAlert className="h-3.5 w-3.5" /> Kill switch active — no new trades today.
            </div>
          ) : enforced ? (
            <div className="mt-2 text-[10px] font-mono text-muted-foreground">
              {fmtUsd(pnl?.remaining)} of today's loss budget left before trading auto-stops.
            </div>
          ) : (
            <div className="mt-2 text-[10px] font-mono text-muted-foreground">
              Demo mode — kill switch is off, this only tracks today's virtual P&L. Trades start at 2 lots and scale up with confidence (max 10).
            </div>
          )}
        </div>

        {/* propose */}
        <div>
          <div className="text-[10px] font-mono uppercase tracking-[0.1em] text-muted-foreground mb-1.5">
            Auto-hunting your active instruments · tap to scan now
          </div>
          <div className="flex gap-2 flex-wrap" data-testid="scan-buttons">
            {symbols.map((s) => (
              <Button
                key={s.value}
                size="sm"
                variant="outline"
                disabled={!!busy || blocked}
                onClick={() => propose(s.value)}
                data-testid={`propose-${s.value}`}
                className="font-mono text-xs"
              >
                {busy === `propose-${s.value}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : `Scan ${s.label}`}
              </Button>
            ))}
          </div>

          {scanStatus.length > 0 && (
            <div className="mt-2 border border-border/40 bg-background rounded-sm divide-y divide-border/30" data-testid="scan-status">
              {scanStatus.map((s) => (
                <div key={s.value} className="flex items-center justify-between gap-2 px-2.5 py-1.5 text-[11px]" data-testid={`scan-status-${s.mt5_symbol}`}>
                  <span className="font-mono flex items-center gap-1.5 shrink-0">
                    <span className={`h-1.5 w-1.5 rounded-full ${s.tradeable ? "bg-buy" : "bg-muted-foreground/50"}`} />
                    {s.label}
                    {s.tradeable && s.side && (
                      <span className={s.side === "BUY" ? "text-buy" : "text-sell"}>{s.side}</span>
                    )}
                  </span>
                  <span className="text-muted-foreground text-right truncate num">
                    {s.tradeable
                      ? `${Math.round((s.win_prob || 0) * 100)}% odds · ${s.hold_label || ""}`
                      : s.reason}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* approval queue */}
        <div>
          <div className="text-[10px] font-mono uppercase tracking-[0.1em] text-muted-foreground mb-1.5">
            Approval queue · {pending.length} awaiting review
          </div>
          {pending.length === 0 ? (
            <div className="text-xs text-muted-foreground font-mono py-4 text-center border border-dashed border-border/40 rounded-sm">
              No trades awaiting approval. Scan Gold or Bitcoin to create one.
            </div>
          ) : (
            <div className="space-y-2" data-testid="approval-queue">
              {pending.map((p) => {
                const isBuy = p.side === "BUY";
                return (
                  <div key={p.id} className="border border-border/50 bg-background rounded-sm p-3" data-testid={`proposal-${p.id}`}>
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className={`font-mono text-sm font-semibold flex items-center gap-1 ${isBuy ? "text-buy" : "text-sell"}`}>
                          {isBuy ? <TrendingUp className="h-4 w-4" /> : <TrendingDown className="h-4 w-4" />}
                          {p.side} {p.mt5_symbol}
                        </span>
                        <Badge variant="outline" className="text-[10px] font-mono">{p.lot} lots</Badge>
                        <span className="text-[10px] font-mono text-muted-foreground">conf {p.confidence}%</span>
                      </div>
                      <span className="text-[10px] font-mono text-muted-foreground">{fmtTime(p.created_at)}</span>
                    </div>
                    <div className="grid grid-cols-3 gap-2 mb-2">
                      <Stat label="Entry ≈" value={fmtNum(p.entry_ref)} />
                      <Stat label="Stop loss" value={fmtNum(p.stop_loss)} cls="text-sell" />
                      <Stat label="Take profit" value={fmtNum(p.take_profit)} cls="text-buy" />
                    </div>
                    <div className="grid grid-cols-3 gap-2 mb-2">
                      <Stat label="Sim win odds" value={p.win_prob != null ? `${Math.round(p.win_prob * 100)}%` : "—"} cls="text-primary" />
                      <Stat label="Expected" value={p.expected_r != null ? `${p.expected_r >= 0 ? "+" : ""}${p.expected_r.toFixed(2)}R` : "—"} cls={pnlColor(p.expected_r)} />
                      <Stat label="R:R" value={p.rr != null ? `${p.rr.toFixed(1)}:1` : "—"} />
                    </div>
                    <div className="grid grid-cols-3 gap-2 mb-2">
                      <Stat label="Risk ≈" value={p.est_risk_usd != null ? fmtUsd(p.est_risk_usd) : "—"} cls="text-sell" />
                      <Stat label="Reward ≈" value={p.est_reward_usd != null ? fmtUsd(p.est_reward_usd) : "—"} cls="text-buy" />
                      <Stat label="Hold ≈" value={p.hold_label || "—"} />
                    </div>
                    {p.reasons?.length > 0 && (
                      <ul className="text-[11px] text-muted-foreground font-mono space-y-0.5 mb-2 list-disc pl-4">
                        {p.reasons.slice(0, 2).map((r, i) => <li key={i}>{r}</li>)}
                      </ul>
                    )}
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        disabled={!!busy}
                        onClick={() => decide(p.id, "approve")}
                        data-testid={`approve-${p.id}`}
                        className="flex-1 font-mono text-xs bg-buy hover:bg-buy/90 text-white"
                      >
                        {busy === `approve-${p.id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <><Check className="h-3.5 w-3.5 mr-1" /> Approve &amp; place</>}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={!!busy}
                        onClick={() => decide(p.id, "reject")}
                        data-testid={`reject-${p.id}`}
                        className="font-mono text-xs"
                      >
                        <X className="h-3.5 w-3.5 mr-1" /> Reject
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* open positions */}
        {positions.length > 0 && (
          <div>
            <div className="text-[10px] font-mono uppercase tracking-[0.1em] text-muted-foreground mb-1.5">
              Open demo positions
            </div>
            <div className="space-y-1.5" data-testid="open-positions">
              {positions.map((p) => (
                <div key={p.id} className="flex items-center justify-between border border-border/40 bg-background rounded-sm px-3 py-2 text-[11px] num" data-testid={`position-${p.id}`}>
                  <span className="font-mono">{p.symbol} {String(p.type || "").includes("SELL") ? "SELL" : "BUY"} {p.volume}</span>
                  <span className="text-muted-foreground">@ {fmtNum(p.open_price)}</span>
                  <span className={pnlColor(p.profit)}>{fmtUsd(p.profit)}</span>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!!busy}
                    onClick={() => closePos(p.id)}
                    data-testid={`close-${p.id}`}
                    className="h-6 font-mono text-[10px]"
                  >
                    {busy === `close-${p.id}` ? <Loader2 className="h-3 w-3 animate-spin" /> : "Close"}
                  </Button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* trade log */}
        <div>
          <div className="text-[10px] font-mono uppercase tracking-[0.1em] text-muted-foreground mb-1.5 flex items-center gap-1.5">
            <ScrollText className="h-3.5 w-3.5" /> Trade log &amp; decisions
          </div>
          <ScrollArea className="h-[140px]">
            {log.length === 0 && history.length === 0 ? (
              <div className="text-xs text-muted-foreground font-mono py-4 text-center">No trades yet.</div>
            ) : (
              <table className="w-full text-[11px] num" data-testid="trade-log">
                <tbody>
                  {log.map((t) => (
                    <tr key={t.id} className="border-b border-border/30">
                      <td className="py-1.5 text-muted-foreground">{fmtTime(t.placed_at)}</td>
                      <td className={t.side === "BUY" ? "text-buy" : "text-sell"}>{t.side} {t.symbol}</td>
                      <td className="text-right">{t.lot} lots</td>
                      <td className="text-right pr-1 text-muted-foreground">{t.status}</td>
                    </tr>
                  ))}
                  {history.map((p) => (
                    <tr key={p.id} className="border-b border-border/30 opacity-60">
                      <td className="py-1.5 text-muted-foreground">{fmtTime(p.decided_at || p.created_at)}</td>
                      <td>{p.side} {p.mt5_symbol}</td>
                      <td className="text-right">—</td>
                      <td className="text-right pr-1 text-muted-foreground">{p.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </ScrollArea>
        </div>

        <p className="text-[10px] font-mono text-muted-foreground leading-relaxed border-t border-border/40 pt-2">
          Demo account only. Trades are placed on virtual funds via MetaApi for practice. Every order needs your explicit approval and is capped at {account?.available ? "" : ""}the risk limits. Not financial advice — live markets carry real risk.
        </p>
      </div>
    </div>
  );
}
