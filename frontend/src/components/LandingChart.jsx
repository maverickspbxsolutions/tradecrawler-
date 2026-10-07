import { useRef, useEffect } from "react";
import { createChart, CandlestickSeries } from "lightweight-charts";

// Decorative Deriv-style candlestick chart for the landing page.
// A "forming" candle ticks live, then commits and a new one starts — purely visual.
export function LandingChart() {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const GREEN = "#00E676";
    const RED = "#FF3366";

    const chart = createChart(containerRef.current, {
      autoSize: true,
      localization: { locale: "en-US" },
      layout: {
        background: { color: "transparent" },
        textColor: "rgba(0,230,118,0.4)",
        fontFamily: "Azeret Mono, monospace",
        fontSize: 10,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "rgba(0,230,118,0.05)" },
        horzLines: { color: "rgba(0,230,118,0.05)" },
      },
      rightPriceScale: { visible: false },
      leftPriceScale: { visible: false },
      timeScale: { visible: false, borderVisible: false, barSpacing: 10 },
      crosshair: {
        vertLine: { visible: false, labelVisible: false },
        horzLine: { visible: false, labelVisible: false },
      },
      handleScroll: false,
      handleScale: false,
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: GREEN,
      downColor: RED,
      wickUpColor: GREEN,
      wickDownColor: RED,
      borderUpColor: GREEN,
      borderDownColor: RED,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    const STEP = 60; // seconds between candles
    let t = Math.floor(Date.now() / 1000) - STEP * 60;
    let price = 100;

    // make one candle via a short random walk with gentle upward drift
    const makeCandle = (time) => {
      const open = price;
      let hi = open;
      let lo = open;
      for (let i = 0; i < 6; i++) {
        price += (Math.random() - 0.44) * 1.8;
        hi = Math.max(hi, price);
        lo = Math.min(lo, price);
      }
      price = Math.max(20, price);
      return { time, open, high: Math.max(hi, price), low: Math.min(lo, price), close: price };
    };

    // seed history
    const seed = [];
    for (let i = 0; i < 60; i++) {
      seed.push(makeCandle(t));
      t += STEP;
    }
    series.setData(seed);
    chart.timeScale().fitContent();

    // live forming candle that ticks, then commits
    let forming = { time: t, open: price, high: price, low: price, close: price };
    let ticks = 0;

    const id = setInterval(() => {
      forming.close += (Math.random() - 0.44) * 1.8;
      forming.close = Math.max(20, forming.close);
      forming.high = Math.max(forming.high, forming.close);
      forming.low = Math.min(forming.low, forming.close);
      series.update(forming);
      ticks += 1;

      if (ticks >= 7) {
        // commit the candle, start a fresh one
        price = forming.close;
        t += STEP;
        forming = { time: t, open: price, high: price, low: price, close: price };
        ticks = 0;
        chart.timeScale().scrollToRealTime();
      }
    }, 280);

    return () => {
      clearInterval(id);
      chart.remove();
    };
  }, []);

  return <div ref={containerRef} style={{ width: "100%", height: "100%" }} data-testid="landing-chart" />;
}
