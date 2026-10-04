import { useRef, useState, useEffect } from "react";
import { useChartTheme } from "@/lib/useChartTheme";

export function Candlestick({ candles, height = 240, liveLast = false }) {
  const theme = useChartTheme();
  const GREEN = theme.buy || "#00E676";
  const RED = theme.sell || "#FF3366";
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
    const y = e.clientY - rect.top;
    const i = Math.floor(x / step);
    if (i >= 0 && i < n) setHover({ i, c: candles[i], x, y });
  };

  const hoverPrice = hover ? min + (1 - (hover.y - padT) / plotH) * (max - min) : null;

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
              <line x1={0} y1={y} x2={plotW} y2={y} stroke={theme.grid} strokeWidth={1} />
              <text x={plotW + 5} y={y + 3} fill={theme.axis} fontSize="10" fontFamily="Azeret Mono">
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
          const isLive = liveLast && i === n - 1;
          return (
            <g key={i}>
              <line x1={x} y1={yH} x2={x} y2={yL} stroke={color} strokeWidth={1} opacity={isLive ? 0.9 : 1} />
              <rect
                x={x - bw / 2}
                y={top}
                width={bw}
                height={bh}
                fill={color}
                fillOpacity={isLive ? 0.4 : 1}
                stroke={isLive ? color : "none"}
                strokeWidth={isLive ? 1 : 0}
                strokeDasharray={isLive ? "2 2" : undefined}
              />
              {isLive && (
                <circle cx={x} cy={yFor(c.c)} r={3} fill={color}>
                  <animate attributeName="opacity" values="1;0.25;1" dur="1.2s" repeatCount="indefinite" />
                </circle>
              )}
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
          opacity={0.7}
        />
        <rect x={plotW} y={yFor(last.c) - 8} width={padR} height={16} fill={lastUp ? GREEN : RED} rx={2} />
        <text x={plotW + padR / 2} y={yFor(last.c) + 3} fill="#FFFFFF" fontSize="10"
              fontFamily="Azeret Mono" textAnchor="middle" fontWeight="600">
          {fmt(last.c)}
        </text>

        {hover && (
          <g pointerEvents="none">
            <line x1={hover.x} y1={padT} x2={hover.x} y2={H - padB} stroke={theme.axis} strokeWidth={0.6} strokeDasharray="2 3" opacity={0.6} />
            <line x1={0} y1={hover.y} x2={plotW} y2={hover.y} stroke={theme.axis} strokeWidth={0.6} strokeDasharray="2 3" opacity={0.6} />
            <rect x={plotW} y={hover.y - 8} width={padR} height={16} fill={theme.axis} rx={2} />
            <text x={plotW + padR / 2} y={hover.y + 3} fill="#FFFFFF" fontSize="10" fontFamily="Azeret Mono" textAnchor="middle">
              {fmt(hoverPrice)}
            </text>
          </g>
        )}
      </svg>

      {hover && (
        <div className="absolute top-1 left-1 bg-popover border border-border rounded-sm px-2 py-1 text-[10px] font-mono num pointer-events-none">
          <span className="text-muted-foreground">{new Date(hover.c.t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>{"  "}
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
