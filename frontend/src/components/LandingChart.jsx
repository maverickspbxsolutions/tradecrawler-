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

    // organic market state: trend momentum drifts & reverses, volatility breathes
    let momentum = 0.15;   // current directional bias (can go negative = pullback)
    let vol = 1.4;         // current volatility, varies over time

    const evolveRegime = () => {
      // momentum mean-reverts toward a slight long-term up-bias with random shocks
      momentum += (Math.random() - 0.5) * 0.5 + (0.1 - momentum) * 0.08;
      momentum = Math.max(-1.1, Math.min(1.3, momentum));
      // occasional volatility spikes & calms
      vol += (Math.random() - 0.5) * 0.6 + (1.3 - vol) * 0.1;
      vol = Math.max(0.6, Math.min(3.2, vol));
      // rare sharp move
      if (Math.random() < 0.06) momentum += (Math.random() - 0.4) * 1.6;
    };

    const tickPrice = () => {
      // intrabar tick: momentum + noise scaled by current volatility
      price += momentum * 0.35 + (Math.random() - 0.5) * vol;
      price = Math.max(20, price);
      return price;
    };

    const makeCandle = (time) => {
      evolveRegime();
      const open = price;
      let hi = open;
      let lo = open;
      const steps = 4 + Math.floor(Math.random() * 5);
      for (let i = 0; i < steps; i++) {
        tickPrice();
        hi = Math.max(hi, price);
        lo = Math.min(lo, price);
      }
      // occasional long wick (rejection)
      if (Math.random() < 0.25) {
        if (Math.random() < 0.5) hi += Math.random() * vol * 2.2;
        else lo -= Math.random() * vol * 2.2;
        lo = Math.max(10, lo);
      }
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
    evolveRegime();
    let forming = { time: t, open: price, high: price, low: price, close: price };
    let ticks = 0;
    const barTicks = 5 + Math.floor(Math.random() * 4);

    let ticksInBar = barTicks;
    const id = setInterval(() => {
      tickPrice();
      forming.close = price;
      forming.high = Math.max(forming.high, price);
      forming.low = Math.min(forming.low, price);
      series.update(forming);
      ticks += 1;

      if (ticks >= ticksInBar) {
        // close candle, maybe add a wick, start a fresh one with a new regime
        if (Math.random() < 0.25) {
          if (Math.random() < 0.5) forming.high += Math.random() * vol * 2;
          else forming.low = Math.max(10, forming.low - Math.random() * vol * 2);
          series.update(forming);
        }
        t += STEP;
        evolveRegime();
        forming = { time: t, open: price, high: price, low: price, close: price };
        ticks = 0;
        ticksInBar = 5 + Math.floor(Math.random() * 4);
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
