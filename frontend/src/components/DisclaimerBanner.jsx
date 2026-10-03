import { TriangleAlert } from "lucide-react";

export function DisclaimerBanner() {
  return (
    <div
      data-testid="disclaimer-banner"
      className="sticky top-0 z-50 w-full bg-warn text-black overflow-hidden"
    >
      <div className="flex items-center gap-3 px-4 py-1.5 text-[10px] sm:text-xs font-mono font-bold uppercase tracking-[0.2em] whitespace-nowrap">
        <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
        Signals are not financial advice · Alerts only · No orders are ever placed · Paper-trading simulation
      </div>
    </div>
  );
}
