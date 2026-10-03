import { useRef, useState, useEffect } from "react";

const GREEN = "#00E676";
const RED = "#FF3366";

export function Candlestick({ candles, height = 240 }) {
  const ref = useRef(null);
  const [w, setW] = useState(800);
  const [hover, setHover] = useState(null);

  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setW(e.contentRect.width);
    });
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);

  if (!candles?.length) {
    return (
      <div ref={ref} style={{ height }} className="grid place-items-center text-xs text-muted-foreground font-mono">
        loading…
      </div>
    );
  }

  const padR = 58;
  const padT = 8;
  const padB = 8;
  const H = height;
  const plotW = Math.max(10, w - padR);
  const plotH = H - padT - padB;

  let min = Math.min(...candles.map((c) => c.l));
  let max = Math.max(...candles.map((c) => c.h));
  const pad = (max - min) * 0.06 || 1;
  min -= pad;
  max += pad;

  const n = candles.length;
  const step = plotW / n;
  const bw = Math.max(1, Math.min(step * 0.68, 12));
  const yFor = (v) => padT + (1 - (v - min) / (max - min)) * plotH;
  const fmt = (v) => (v < 10 ? v.toFixed(4) : v.toLocaleString(undefined, { maximumFractionDigits: 2 }));

  const ticks = 5;
  const tickVals = [...Array(ticks)].map((_, i) => min + ((max - min) * i) / (ticks - 1));

  const last = candles[n - 1];
  const lastUp = last.c >= last.o;

  const onMove = (e) => {
    const rect = ref.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const i = Math.floor(x / step);
    if (i >= 0 && i < n) setHover({ i, c: candles[i] });
  };

  return (
    <div
      ref={ref}
      className="relative"
      style={{ height }}
      onMouseMove={onMove}
      onMouseLeave={() => setHover(null)}
      data-testid="candlestick-chart"
    >
      <svg width="100%" height={H} style={{ display: "block" }}>
        {tickVals.map((v, i) => {
          const y = yFor(v);
          return (
            <g key={i}>
              <line x1={0} y1={y} x2={plotW} y2={y} stroke="#1F1F24" strokeWidth={1} />
              <text x={plotW + 5} y={y + 3} fill="#8A8A93" fontSize="10" fontFamily="Azeret Mono">
                {fmt(v)}
              </text>
            </g>
          );
        })}

        {candles.map((c, i) => {
          const x = i * step + step / 2;
          const up = c.c >= c.o;
          const color = up ? GREEN : RED;
          const yH = yFor(c.h);
          const yL = yFor(c.l);
          const top = Math.min(yFor(c.o), yFor(c.c));
          const bh = Math.max(1, Math.abs(yFor(c.c) - yFor(c.o)));
          return (
            <g key={i}>
              <line x1={x} y1={yH} x2={x} y2={yL} stroke={color} strokeWidth={1} />
              <rect x={x - bw / 2} y={top} width={bw} height={bh} fill={color} />
            </g>
          );
        })}

        <line
          x1={0}
          y1={yFor(last.c)}
          x2={plotW}
          y2={yFor(last.c)}
          stroke={lastUp ? GREEN : RED}
          strokeWidth={1}
          strokeDasharray="3 3"
          opacity={0.6}
        />
      </svg>

      {hover && (
        <div className="absolute top-1 left-1 bg-[#121215] border border-border/60 rounded-sm px-2 py-1 text-[10px] font-mono num pointer-events-none">
          <span className="text-muted-foreground">O</span> {fmt(hover.c.o)}{" "}
          <span className="text-muted-foreground">H</span> <span className="text-buy">{fmt(hover.c.h)}</span>{" "}
          <span className="text-muted-foreground">L</span> <span className="text-sell">{fmt(hover.c.l)}</span>{" "}
          <span className="text-muted-foreground">C</span>{" "}
          <span style={{ color: hover.c.c >= hover.c.o ? GREEN : RED }}>{fmt(hover.c.c)}</span>
        </div>
      )}
    </div>
  );
}
