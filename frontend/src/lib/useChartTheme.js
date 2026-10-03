import { useMemo } from "react";
import { useTheme } from "next-themes";

function v(name) {
  if (typeof window === "undefined") return "";
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return raw ? `hsl(${raw})` : "";
}

// Theme-aware colors for SVG / recharts (which can't read CSS classes).
// Recomputes whenever the resolved theme changes.
export function useChartTheme() {
  const { resolvedTheme } = useTheme();
  return useMemo(
    () => ({
      grid: v("--border"),
      axis: v("--muted-foreground"),
      tooltipBg: v("--popover"),
      tooltipText: v("--popover-foreground"),
      tooltipBorder: v("--border"),
      buy: v("--buy"),
      sell: v("--sell"),
      muted: v("--muted-foreground"),
      accent: v("--primary"),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [resolvedTheme]
  );
}
