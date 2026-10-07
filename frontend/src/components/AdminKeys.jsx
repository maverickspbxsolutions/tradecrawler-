import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Copy, Ban, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";

const TIERS = ["basic", "moderate", "advanced"];
const DURS = { "1m": "1 month", "3m": "3 months", "6m": "6 months", "1y": "1 year" };

export function AdminKeys() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [tier, setTier] = useState("advanced");
  const [duration, setDuration] = useState("1m");
  const [count, setCount] = useState(1);

  const { data } = useQuery({
    queryKey: ["admin-keys"],
    queryFn: async () => (await api.get("/admin/keys")).data,
    enabled: open,
  });
  const mint = useMutation({
    mutationFn: async () => (await api.post("/admin/keys", { tier, duration, count: Number(count) })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-keys"] }),
  });
  const revoke = useMutation({
    mutationFn: async (key) => (await api.post(`/admin/keys/${key}/revoke`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-keys"] }),
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="h-8 rounded-sm border-primary/40 text-primary" data-testid="admin-keys-btn">
          <KeyRound className="h-3.5 w-3.5 mr-1.5" /> Keys
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl" data-testid="admin-keys-dialog">
        <DialogHeader><DialogTitle>License Keys</DialogTitle></DialogHeader>

        <div className="flex items-end gap-2 border-b border-border/40 pb-4">
          <div className="flex-1">
            <label className="text-[10px] font-mono uppercase text-muted-foreground">Plan</label>
            <Select value={tier} onValueChange={setTier}>
              <SelectTrigger className="h-9 rounded-sm mt-1" data-testid="mint-tier"><SelectValue /></SelectTrigger>
              <SelectContent>{TIERS.map((t) => <SelectItem key={t} value={t} className="capitalize">{t}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="flex-1">
            <label className="text-[10px] font-mono uppercase text-muted-foreground">Length</label>
            <Select value={duration} onValueChange={setDuration}>
              <SelectTrigger className="h-9 rounded-sm mt-1" data-testid="mint-duration"><SelectValue /></SelectTrigger>
              <SelectContent>{Object.entries(DURS).map(([k, v]) => <SelectItem key={k} value={k}>{v}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="w-20">
            <label className="text-[10px] font-mono uppercase text-muted-foreground">How many</label>
            <input type="number" min="1" max="100" value={count} onChange={(e) => setCount(e.target.value)}
              className="h-9 w-full rounded-sm mt-1 bg-background border border-border px-2 text-sm" data-testid="mint-count" />
          </div>
          <Button onClick={() => mint.mutate()} disabled={mint.isPending} className="h-9 rounded-sm" data-testid="mint-btn">
            {mint.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Generate"}
          </Button>
        </div>

        <div className="max-h-80 overflow-y-auto divide-y divide-border/30">
          {(data?.keys || []).map((k) => (
            <div key={k.key} className="flex items-center justify-between py-2 text-xs font-mono" data-testid={`key-row-${k.key}`}>
              <div className="flex items-center gap-2">
                <button onClick={() => navigator.clipboard.writeText(k.key)} className="text-muted-foreground hover:text-foreground" title="Copy">
                  <Copy className="h-3 w-3" />
                </button>
                <span className="text-primary">{k.key}</span>
                <span className="capitalize text-muted-foreground">{k.tier} · {DURS[k.duration]}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className={k.status === "unused" ? "text-buy" : k.status === "revoked" ? "text-sell" : "text-muted-foreground"}>
                  {k.status === "redeemed" ? `used by ${k.redeemed_username || "?"}` : k.status}
                </span>
                {k.status === "unused" && (
                  <button onClick={() => revoke.mutate(k.key)} className="text-sell hover:opacity-70" title="Revoke" data-testid={`revoke-${k.key}`}>
                    <Ban className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            </div>
          ))}
          {data && data.keys?.length === 0 && <p className="text-xs text-muted-foreground py-4 text-center">No keys yet. Generate some above.</p>}
        </div>
      </DialogContent>
    </Dialog>
  );
}
