import { useQuery } from "@tanstack/react-query";
import { Eye, Mail, Bell, Spline, TriangleAlert, CheckCircle2 } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";

function Step({ icon: Icon, title, text }) {
  return (
    <div className="flex items-start gap-3 flex-1 min-w-[200px]">
      <div className="h-8 w-8 shrink-0 grid place-items-center border border-primary/40 bg-primary/10 rounded-sm text-primary">
        <Icon className="h-4 w-4" />
      </div>
      <div>
        <div className="text-sm font-medium">{title}</div>
        <div className="text-[12px] text-muted-foreground leading-snug">{text}</div>
      </div>
    </div>
  );
}

export function HowItWorks({ onOpenSettings }) {
  const { data: settings } = useQuery({
    queryKey: ["settings"],
    queryFn: async () => (await api.get("/settings")).data,
    refetchInterval: 30000,
  });
  const emails = settings?.alert_emails || [];
  const hasEmail = emails.length > 0;

  return (
    <div className="border border-border/40 bg-card rounded-sm p-4 mb-3" data-testid="how-it-works">
      <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
        <h2 className="text-lg font-semibold tracking-tight">
          You don't have to do anything — TradeCrawler watches the market for you, 24/7.
        </h2>
        {hasEmail ? (
          <span className="flex items-center gap-1.5 text-[11px] font-mono text-buy border border-buy/40 rounded-sm px-2 py-1" data-testid="email-status-ok">
            <CheckCircle2 className="h-3.5 w-3.5" /> Alerts going to {emails.length} email{emails.length > 1 ? "s" : ""}
          </span>
        ) : (
          <Button size="sm" className="h-8 rounded-sm bg-warn text-black hover:bg-warn/90 font-medium" onClick={onOpenSettings} data-testid="add-email-nudge">
            <TriangleAlert className="h-3.5 w-3.5 mr-1.5" /> Add your email to get alerts
          </Button>
        )}
      </div>

      <div className="flex flex-wrap gap-5">
        <Step icon={Mail} title="1 · Add your email" text="Tell TradeCrawler where to reach you. Add as many emails as you like." />
        <Step icon={Eye} title="2 · It watches for you" text="It checks live crypto prices every 30 seconds, round the clock." />
        <Step icon={Bell} title="3 · You get a plan" text="When it finds a good ~$20 trade, you get an email: what to buy, your target, and when to sell." />
        <Step icon={Spline} title="4 · It keeps learning" text="It re-tests itself daily and keeps only the settings that actually work." />
      </div>
    </div>
  );
}
