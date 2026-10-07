import { useState } from "react";
import { Loader2, KeyRound, ShieldAlert, Clock, Mail, LogOut } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { SpiderIcon } from "@/components/SpiderIcon";
import { formatApiErrorDetail } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

function KeyForm({ cta }) {
  const { redeem } = useAuth();
  const [key, setKey] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setErr(""); setBusy(true);
    try { await redeem(key); } catch (e2) { setErr(formatApiErrorDetail(e2.response?.data?.detail) || "Could not activate key"); }
    finally { setBusy(false); }
  };
  return (
    <form onSubmit={submit} className="w-full space-y-3" data-testid="redeem-form">
      <Input value={key} onChange={(e) => setKey(e.target.value.toUpperCase())} placeholder="TC-XXXX-XXXX-XXXX-XXXX"
        className="h-10 rounded-sm font-mono text-center" data-testid="redeem-input" />
      {err && <div className="text-xs text-sell font-mono" data-testid="redeem-error">{err}</div>}
      <Button type="submit" disabled={busy} className="w-full h-10 rounded-sm" data-testid="redeem-btn">
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : cta}
      </Button>
    </form>
  );
}

function Shell({ icon: Icon, tone, title, children }) {
  const { logout, user } = useAuth();
  return (
    <div className="min-h-screen grid place-items-center bg-background p-6">
      <div className="w-full max-w-md border border-border/50 bg-card rounded-sm p-8" data-testid="license-gate">
        <div className="flex items-center gap-3 mb-6">
          <div className="h-10 w-10 grid place-items-center border border-primary/40 bg-primary/10 rounded-sm">
            <SpiderIcon className="h-6 w-6 text-primary" />
          </div>
          <div className="text-lg font-semibold tracking-tight">TradeCrawler</div>
        </div>
        <Icon className={`h-9 w-9 mb-3 ${tone}`} />
        <h1 className="text-xl font-semibold mb-2">{title}</h1>
        {children}
        <button onClick={logout} className="flex items-center gap-1.5 text-[11px] font-mono text-muted-foreground hover:text-foreground mt-6" data-testid="gate-logout">
          <LogOut className="h-3 w-3" /> Sign out ({user?.email || user?.username})
        </button>
      </div>
    </div>
  );
}

export function LicenseGate({ children }) {
  const { user } = useAuth();
  const lic = user?.license || {};
  const rank = lic.tier_rank || 0;

  if (lic.is_admin || (lic.status === "active" && rank >= 2)) return children; // moderate+ & admin

  if (lic.status === "active" && rank === 1) {
    return (
      <Shell icon={Mail} tone="text-primary" title="You're on the Basic plan">
        <p className="text-sm text-muted-foreground leading-relaxed mb-4">
          Your trade alerts and a short preview of where the market may head are sent straight to your email —
          nothing to do here. Want the live dashboard, demo trades and full access? Upgrade with a Moderate or
          Advanced key below.
        </p>
        <KeyForm cta="Upgrade with a key" />
        <p className="text-[11px] font-mono text-muted-foreground mt-3">Plan active · {lic.days_left} days left.</p>
      </Shell>
    );
  }

  if (lic.status === "restricted") {
    return (
      <Shell icon={Clock} tone="text-amber-500" title="Your access has paused">
        <p className="text-sm text-muted-foreground leading-relaxed mb-4">
          Your license ran out, so alerts and the dashboard are paused. Your account and history are safe — just
          enter a renewal key to pick up where you left off. If you don't renew within <b>7 days</b> the account
          is locked.
        </p>
        <KeyForm cta="Renew with a key" />
      </Shell>
    );
  }

  if (lic.status === "disabled") {
    return (
      <Shell icon={ShieldAlert} tone="text-sell" title="Account locked">
        <p className="text-sm text-muted-foreground leading-relaxed mb-4">
          This account has been inactive past the renewal window and is now locked. Enter a valid key to
          reactivate it, or contact the admin.
        </p>
        <KeyForm cta="Reactivate with a key" />
      </Shell>
    );
  }

  // inactive / no tier yet
  return (
    <Shell icon={KeyRound} tone="text-primary" title="Activate your access">
      <p className="text-sm text-muted-foreground leading-relaxed mb-4">
        Enter the license key you were given to unlock TradeCrawler. Each key comes with a plan (Basic, Moderate or
        Advanced) and a length of time.
      </p>
      <KeyForm cta="Activate" />
    </Shell>
  );
}
