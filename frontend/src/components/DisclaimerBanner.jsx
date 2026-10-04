import { TriangleAlert } from "lucide-react";

export function DisclaimerBanner() {
  return (
    <div
      data-testid="disclaimer-banner"
      className="sticky top-0 z-50 w-full text-black overflow-hidden"
      style={{ backgroundColor: "hsl(var(--warn))" }}
    >
      <div className="flex items-center gap-3 px-4 py-1.5 text-[10px] sm:text-xs font-mono font-bold uppercase tracking-[0.2em] whitespace-nowrap">
        <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
        Not financial advice · Demo trading on virtual funds · Every trade needs your approval · Live markets carry real risk
      </div>
    </div>
  );
}
