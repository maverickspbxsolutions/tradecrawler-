import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Mail, Send, MessageSquare } from "lucide-react";
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
import { Switch } from "@/components/ui/switch";

export function SettingsDialog({ open, onOpenChange }) {
  const qc = useQueryClient();
  const [form, setForm] = useState(null);

  const { data } = useQuery({
    queryKey: ["settings"],
    queryFn: async () => (await api.get("/settings")).data,
    enabled: open,
  });

  useEffect(() => {
    if (data) setForm(data);
  }, [data]);

  const save = useMutation({
    mutationFn: (body) => api.put("/settings", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      toast.success("Settings saved");
    },
    onError: () => toast.error("Save failed"),
  });

  const test = useMutation({
    mutationFn: () => api.post("/alerts/test"),
    onSuccess: () => toast.success("Test email sent"),
    onError: (e) => toast.error(e.response?.data?.detail || "Could not send"),
  });

  if (!form) return null;
  const set = (k, v) => setForm({ ...form, [k]: v });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg rounded-sm" data-testid="settings-dialog">
        <DialogHeader>
          <DialogTitle className="text-base flex items-center gap-2">
            <Mail className="h-4 w-4" /> Alert Settings
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <Label className="text-[10px] font-mono uppercase text-muted-foreground">Alert Email</Label>
            <div className="flex gap-2 mt-0.5">
              <Input
                value={form.alert_email || ""}
                onChange={(e) => set("alert_email", e.target.value)}
                placeholder="you@email.com"
                className="h-9 rounded-sm"
                data-testid="alert-email-input"
              />
              <Button variant="outline" className="h-9 rounded-sm" onClick={() => test.mutate()} disabled={test.isPending} data-testid="test-email-btn">
                <Send className="h-3.5 w-3.5 mr-1" /> Test
              </Button>
            </div>
            <p className="text-[10px] text-muted-foreground mt-1">Signal alerts send only for strategies that are Live and have passed the backtest gate. Sent from your own Resend account. To deliver to any inbox, verify a domain in Resend and set RESEND_FROM_EMAIL.</p>
          </div>

          <div className="flex items-center justify-between border border-border/40 bg-background rounded-sm px-3 py-2">
            <span className="text-xs">Email alerts enabled</span>
            <Switch checked={form.email_enabled} onCheckedChange={(v) => set("email_enabled", v)} data-testid="email-enabled-switch" />
          </div>

          <div className="flex items-center justify-between border border-border/40 bg-background rounded-sm px-3 py-2 opacity-70">
            <span className="text-xs flex items-center gap-2">
              <MessageSquare className="h-3.5 w-3.5" /> SMS alerts (Twilio)
            </span>
            <span className="text-[10px] font-mono uppercase text-muted-foreground">coming soon</span>
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div>
              <Label className="text-[9px] font-mono uppercase text-muted-foreground">Alerts / hour</Label>
              <Input type="number" value={form.max_alerts_per_hour} onChange={(e) => set("max_alerts_per_hour", parseInt(e.target.value))} className="h-8 rounded-sm num text-xs mt-0.5" />
            </div>
            <div>
              <Label className="text-[9px] font-mono uppercase text-muted-foreground">Emails / day</Label>
              <Input type="number" value={form.max_emails_per_day} onChange={(e) => set("max_emails_per_day", parseInt(e.target.value))} className="h-8 rounded-sm num text-xs mt-0.5" />
            </div>
            <div>
              <Label className="text-[9px] font-mono uppercase text-muted-foreground">Cooldown (min)</Label>
              <Input type="number" value={form.min_minutes_between_same_strategy} onChange={(e) => set("min_minutes_between_same_strategy", parseInt(e.target.value))} className="h-8 rounded-sm num text-xs mt-0.5" />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" className="rounded-sm" onClick={() => onOpenChange(false)}>Close</Button>
          <Button className="rounded-sm bg-primary text-primary-foreground hover:bg-primary/90" onClick={() => save.mutate(form)} disabled={save.isPending} data-testid="save-settings-btn">
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
