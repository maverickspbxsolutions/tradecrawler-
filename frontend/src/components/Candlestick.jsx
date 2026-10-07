import { useRef, useEffect, useMemo } from "react";
import {
  createChart,
  CandlestickSeries,
  CrosshairMode,
  LineStyle,
} from "lightweight-charts";
import { useChartTheme } from "@/lib/useChartTheme";

// Deriv / TradingView-style candlestick chart powered by lightweight-charts.
// Props kept identical to the previous SVG component so callers don't change.
export function Candlestick({ candles, height = 240, liveLast = false }) {
  const theme = useChartTheme();
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const lastKeyRef = useRef(null);

  const GREEN = theme.buy || "#00E676";
  const RED = theme.sell || "#FF3366";

  // normalize backend candles -> lightweight-charts points (time in seconds, ascending, unique)
  const data = useMemo(() => {
    if (!candles?.length) return [];
    const seen = new Map();
    for (const c of candles) {
      const time = Math.floor(c.t / 1000);
      seen.set(time, { time, open: c.o, high: c.h, low: c.l, close: c.c });
    }
    return Array.from(seen.values()).sort((a, b) => a.time - b.time);
  }, [candles]);

  const precision = useMemo(() => {
    const last = data.length ? data[data.length - 1].close : 100;
    return last < 10 ? 4 : 2;
  }, [data]);

  // create the chart once (re-create when theme flips light/dark)
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      localization: { locale: "en-US" },
      layout: {
        background: { color: "transparent" },
        textColor: theme.axis || "#9aa4b2",
        fontFamily: "Azeret Mono, monospace",
        fontSize: 10,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: theme.grid || "rgba(255,255,255,0.05)" },
        horzLines: { color: theme.grid || "rgba(255,255,255,0.05)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: theme.axis, width: 1, style: LineStyle.Dashed, labelBackgroundColor: theme.accent || "#2962FF" },
        horzLine: { color: theme.axis, width: 1, style: LineStyle.Dashed, labelBackgroundColor: theme.accent || "#2962FF" },
      },
      rightPriceScale: {
        borderColor: theme.grid || "rgba(255,255,255,0.08)",
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: theme.grid || "rgba(255,255,255,0.08)",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
      },
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: GREEN,
      downColor: RED,
      wickUpColor: GREEN,
      wickDownColor: RED,
      borderUpColor: GREEN,
      borderDownColor: RED,
      priceFormat: { type: "price", precision, minMove: precision === 4 ? 0.0001 : 0.01 },
    });

    chartRef.current = chart;
    seriesRef.current = series;
    lastKeyRef.current = null;

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme.buy, theme.sell, theme.grid, theme.axis]);

  // feed data: full setData on structural change, update() for the live tail
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !data.length) return;

    series.applyOptions({
      priceFormat: { type: "price", precision, minMove: precision === 4 ? 0.0001 : 0.01 },
    });

    const firstT = data[0].time;
    const structuralKey = `${firstT}:${data.length}`;
    const prevKey = lastKeyRef.current;

    if (prevKey === structuralKey) {
      // only the last bar changed -> smooth live update, keeps zoom/scroll
      series.update(data[data.length - 1]);
    } else {
      const isFirst = prevKey === null;
      series.setData(data);
      lastKeyRef.current = structuralKey;
      if (isFirst && chart) chart.timeScale().fitContent();
    }
  }, [data, precision]);

  return (
    <div
      ref={containerRef}
      style={{ height, width: "100%" }}
      data-testid="candlestick-chart"
    />
  );
}
