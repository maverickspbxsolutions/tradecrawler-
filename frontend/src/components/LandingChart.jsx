import { useRef, useEffect } from "react";
import { createChart, AreaSeries, LineStyle } from "lightweight-charts";

// Decorative, continuously-growing scrolling chart for the landing page.
// Data is synthetic (an upward-trending random walk) — purely for visual flair.
export function LandingChart() {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      autoSize: true,
      localization: { locale: "en-US" },
      layout: {
        background: { color: "transparent" },
        textColor: "rgba(0,230,118,0.45)",
        fontFamily: "Azeret Mono, monospace",
        fontSize: 10,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "rgba(0,230,118,0.06)" },
        horzLines: { color: "rgba(0,230,118,0.06)" },
      },
      rightPriceScale: { visible: false },
      leftPriceScale: { visible: false },
      timeScale: { visible: false, borderVisible: false },
      crosshair: {
        vertLine: { visible: false, labelVisible: false },
        horzLine: { visible: false, labelVisible: false },
      },
      handleScroll: false,
      handleScale: false,
    });

    const series = chart.addSeries(AreaSeries, {
      lineColor: "#00E676",
      topColor: "rgba(0,230,118,0.35)",
      bottomColor: "rgba(0,230,118,0.0)",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    // seed an upward-trending series
    const STEP = 60; // seconds between synthetic points
    let t = Math.floor(Date.now() / 1000) - STEP * 120;
    let value = 100;
    const seed = [];
    for (let i = 0; i < 120; i++) {
      value += (Math.random() - 0.42) * 2.4 + 0.25; // gentle upward drift
      value = Math.max(20, value);
      seed.push({ time: t, value });
      t += STEP;
    }
    series.setData(seed);
    chart.timeScale().fitContent();

    // keep growing + scrolling
    const id = setInterval(() => {
      t += STEP;
      value += (Math.random() - 0.42) * 2.4 + 0.25;
      value = Math.max(20, value);
      series.update({ time: t, value });
      chart.timeScale().scrollToRealTime();
    }, 850);

    return () => {
      clearInterval(id);
      chart.remove();
    };
  }, []);

  return <div ref={containerRef} style={{ width: "100%", height: "100%" }} data-testid="landing-chart" />;
}
