import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Mail, Send, MessageSquare, Plus, X } from "lucide-react";
import { api } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

export function SettingsDialog({ open, onOpenChange }) {
  const qc = useQueryClient();
  const [form, setForm] = useState(null);
  const [newEmail, setNewEmail] = useState("");

  const { data } = useQuery({
    queryKey: ["settings"],
    queryFn: async () => (await api.get("/settings")).data,
    enabled: open,
  });

  useEffect(() => {
    if (data) setForm({ ...data, alert_emails: data.alert_emails || [] });
  }, [data]);

  const save = useMutation({
    mutationFn: (body) => api.put("/settings", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      toast.success("Saved");
    },
    onError: () => toast.error("Save failed"),
  });

  const test = useMutation({
    mutationFn: () => api.post("/alerts/test"),
    onSuccess: (r) => toast.success(`Test email sent to ${r.data.recipients?.length || 0} address(es)`),
    onError: (e) => toast.error(e.response?.data?.detail || "Could not send"),
  });

  if (!form) return null;
  const set = (k, v) => setForm({ ...form, [k]: v });

  const addEmail = () => {
    const e = newEmail.trim();
    if (!e || !e.includes("@")) return toast.error("Enter a valid email");
    if (form.alert_emails.includes(e)) return toast.error("Already added");
    const next = [...form.alert_emails, e];
    setForm({ ...form, alert_emails: next });
    setNewEmail("");
    save.mutate({ alert_emails: next });
  };

  const removeEmail = (e) => {
    const next = form.alert_emails.filter((x) => x !== e);
    setForm({ ...form, alert_emails: next });
    save.mutate({ alert_emails: next });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg rounded-sm" data-testid="settings-dialog">
        <DialogHeader>
          <DialogTitle className="text-base flex items-center gap-2">
            <Mail className="h-4 w-4" /> Where should TradeCrawler send alerts?
          </DialogTitle>
          <DialogDescription className="text-xs">
            Add any email addresses you want. TradeCrawler emails all of them the moment it finds a good trade.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <Label className="text-[10px] font-mono uppercase text-muted-foreground">Alert emails</Label>
            <div className="flex gap-2 mt-1">
              <Input
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addEmail())}
                placeholder="add an email…"
                className="h-9 rounded-sm"
                data-testid="alert-email-input"
              />
              <Button variant="outline" className="h-9 rounded-sm" onClick={addEmail} data-testid="add-email-btn">
                <Plus className="h-3.5 w-3.5 mr-1" /> Add
              </Button>
            </div>

            <div className="flex flex-wrap gap-2 mt-2" data-testid="email-list">
              {form.alert_emails.length === 0 ? (
                <span className="text-[11px] text-muted-foreground font-mono">No emails yet — add one above to start getting alerts.</span>
              ) : (
                form.alert_emails.map((e) => (
                  <span key={e} className="flex items-center gap-1.5 text-xs font-mono bg-background border border-border/60 rounded-sm px-2 py-1" data-testid={`email-chip-${e}`}>
                    {e}
                    <button onClick={() => removeEmail(e)} className="text-sell hover:opacity-80" aria-label="Remove">
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                ))
              )}
            </div>

            <div className="flex items-center justify-between mt-3">
              <p className="text-[10px] text-muted-foreground max-w-[70%]">
                Sent from your Resend account. To reach any inbox, verify a domain in Resend and set RESEND_FROM_EMAIL.
              </p>
              <Button variant="outline" className="h-8 rounded-sm" onClick={() => test.mutate()} disabled={test.isPending} data-testid="test-email-btn">
                <Send className="h-3.5 w-3.5 mr-1" /> Send test
              </Button>
            </div>
          </div>

          <div className="flex items-center justify-between border border-border/40 bg-background rounded-sm px-3 py-2">
            <span className="text-xs">Email alerts on</span>
            <Switch checked={form.email_enabled} onCheckedChange={(v) => { set("email_enabled", v); save.mutate({ email_enabled: v }); }} data-testid="email-enabled-switch" />
          </div>

          <div className="flex items-center justify-between border border-border/40 bg-background rounded-sm px-3 py-2 opacity-70">
            <span className="text-xs flex items-center gap-2">
              <MessageSquare className="h-3.5 w-3.5" /> Text message alerts
            </span>
            <span className="text-[10px] font-mono uppercase text-muted-foreground">coming soon</span>
          </div>

          <div>
            <Label className="text-[10px] font-mono uppercase text-muted-foreground">Don't spam me — limits</Label>
            <div className="grid grid-cols-3 gap-2 mt-1">
              <div>
                <span className="text-[9px] font-mono uppercase text-muted-foreground">Max/hour</span>
                <Input type="number" value={form.max_alerts_per_hour} onChange={(e) => set("max_alerts_per_hour", parseInt(e.target.value))} className="h-8 rounded-sm num text-xs mt-0.5" />
              </div>
              <div>
                <span className="text-[9px] font-mono uppercase text-muted-foreground">Max/day</span>
                <Input type="number" value={form.max_emails_per_day} onChange={(e) => set("max_emails_per_day", parseInt(e.target.value))} className="h-8 rounded-sm num text-xs mt-0.5" />
              </div>
              <div>
                <span className="text-[9px] font-mono uppercase text-muted-foreground">Rest (min)</span>
                <Input type="number" value={form.min_minutes_between_same_strategy} onChange={(e) => set("min_minutes_between_same_strategy", parseInt(e.target.value))} className="h-8 rounded-sm num text-xs mt-0.5" />
              </div>
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" className="rounded-sm" onClick={() => onOpenChange(false)}>Close</Button>
          <Button className="rounded-sm bg-primary text-primary-foreground hover:bg-primary/90" onClick={() => save.mutate(form)} disabled={save.isPending} data-testid="save-settings-btn">
            Save limits
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
