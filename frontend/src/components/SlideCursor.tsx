import { RefObject, useEffect, useMemo, useRef } from "react";
import type { PointerLine } from "../audio/playbackQueue";
import { ActivePointer } from "../session/types";

/**
 * Live "reading" highlighter + human-like mouse arrow.
 *  - the arrow travels on a curved ease-in-out path to the start of the line being talked about,
 *  - a marker then paints over the line(s) in step with the voice (strong fill = already said),
 * Coordinates are normalised (0..1) to the slide image; everything is recomputed per frame so resizing is safe.
 */
type P = [number, number];
interface Phase {
  t0: number;
  dur: number;
  from: P;
  to: P;
  ctrl?: P;
  kind: "travel" | "hold" | "sweep";
}

const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));

function contentRect(img: HTMLImageElement) {
  const ew = img.clientWidth;
  const eh = img.clientHeight;
  const ar = img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight : ew / (eh || 1);
  const w = Math.min(ew, eh * ar);
  const h = w / ar;
  return { left: img.offsetLeft + (ew - w) / 2, top: img.offsetTop + (eh - h) / 2, w, h };
}

function linesOf(p: ActivePointer): PointerLine[] {
  return p.lines && p.lines.length ? p.lines : [{ x0: p.x0, y0: p.y0, x1: p.x1, y1: p.y1, words: [] }];
}

export default function SlideCursor({
  imgRef,
  pointer,
  visible,
}: {
  imgRef: RefObject<HTMLImageElement>;
  pointer: ActivePointer | null;
  visible: boolean;
}) {
  const layer = useRef<HTMLDivElement>(null);
  const arrow = useRef<HTMLDivElement>(null);
  const pos = useRef<P>([0.97, 0.95]);
  const started = useRef(false);
  const phases = useRef<Phase[]>([]);
  const cur = useRef<{ lines: PointerLine[]; cum: number[] } | null>(null); // active lines + cumulative width fractions
  const landed = useRef(false);
  const seed = useRef(Math.random() * 1000);
  const lastT = useRef(performance.now());
  const lines = useMemo(() => (pointer ? linesOf(pointer) : []), [pointer?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  // schedule a new move whenever the spoken target changes
  useEffect(() => {
    const img = imgRef.current;
    if (!pointer || !img || !lines.length) {
      cur.current = null;
      landed.current = false;
      phases.current = [];
      return;
    }
    const cr = contentRect(img);
    const widths = lines.map((l) => Math.max(1e-4, l.x1 - l.x0));
    const total = widths.reduce((a, b) => a + b, 0);
    let acc = 0;
    const cum = [0, ...widths.map((w) => (acc += w / total))];
    cur.current = { lines, cum };
    landed.current = false;

    const l0 = lines[0];
    const h = l0.y1 - l0.y0;
    const padX = Math.min(0.012, (l0.x1 - l0.x0) * 0.04);
    const start: P = [l0.x0 + padX, l0.y0 + h * 0.78];
    const from = pos.current;
    const dx = (start[0] - from[0]) * cr.w;
    const dy = (start[1] - from[1]) * cr.h;
    const dist = Math.hypot(dx, dy);
    const now = performance.now();
    const travel = started.current ? clamp(520 + dist * 0.9, 560, 1400) : 1000;
    const bend = (Math.random() < 0.5 ? -1 : 1) * clamp(dist * 0.18, 8, 90); // curved, never a straight laser line
    const nx = dist ? -dy / dist : 0;
    const ny = dist ? dx / dist : 0;
    const ctrl: P = [(from[0] + start[0]) / 2 + (nx * bend) / cr.w, (from[1] + start[1]) / 2 + (ny * bend) / cr.h];
    const hold = 140;
    const sweep = clamp(pointer.durationMs - travel - hold - 150, 500, 20000);
    phases.current = [
      { t0: now, dur: travel, from, to: start, ctrl, kind: "travel" },
      { t0: now + travel, dur: hold, from: start, to: start, kind: "hold" },
      { t0: now + travel + hold, dur: sweep, from: start, to: start, kind: "sweep" },
    ];
    started.current = true;
  }, [pointer?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  // one animation loop for the lifetime of the component
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const img = imgRef.current;
      const el = arrow.current;
      const root = layer.current;
      if (!img || !el || !root) return;
      const t = performance.now();
      const dt = Math.min(0.1, (t - lastT.current) / 1000);
      lastT.current = t;
      const cr = contentRect(img);
      let [x, y] = pos.current;
      const c = cur.current;
      const phs = phases.current;
      const ph = phs.find((p) => t >= p.t0 && t < p.t0 + p.dur) ?? null;
      const last = phs[phs.length - 1];
      let progress = 0; // 0..1 across all highlighted lines

      if (ph && ph.kind === "travel" && ph.ctrl) {
        const e = ease(clamp((t - ph.t0) / ph.dur, 0, 1));
        const a = (1 - e) * (1 - e);
        const b = 2 * (1 - e) * e;
        const cc = e * e;
        x = a * ph.from[0] + b * ph.ctrl[0] + cc * ph.to[0];
        y = a * ph.from[1] + b * ph.ctrl[1] + cc * ph.to[1];
        pos.current = [x, y];
      } else if (c && last && t >= phs[1].t0) {
        if (!landed.current) {
          landed.current = true;
          el.firstElementChild?.animate(
            [{ transform: "scale(1)" }, { transform: "scale(0.82)" }, { transform: "scale(1)" }],
            { duration: 220, easing: "ease-out" },
          ); // a "click" as it lands on the line
        }
        const sweepStart = phs[2].t0;
        const u = clamp((t - sweepStart) / phs[2].dur, 0, 1);
        progress = t < sweepStart ? 0 : 0.88 * u + 0.12 * (u * u * (3 - 2 * u)); // ~linear, slightly human
        // arrow tip rides the leading edge of the marker
        let i = c.lines.length - 1;
        for (let k = 0; k < c.lines.length; k++) if (progress <= c.cum[k + 1]) { i = k; break; }
        const L = c.lines[i];
        const lp = clamp((progress - c.cum[i]) / Math.max(1e-6, c.cum[i + 1] - c.cum[i]), 0, 1);
        const tx = L.x0 + (L.x1 - L.x0) * lp;
        const ty = L.y0 + (L.y1 - L.y0) * 0.78 + Math.sin(u * Math.PI * 5 + seed.current) * 0.0022;
        const k = u >= 1 ? 1 : Math.min(1, dt * 9); // smooth hops between lines
        x += (tx - x) * k;
        y += (ty - y) * k;
        pos.current = [x, y];
        if (u >= 1) {
          x += Math.sin(t / 900 + seed.current) * 0.0012; // resting hand tremor
          y += Math.cos(t / 1100 + seed.current) * 0.0018;
        }
      }
      el.style.transform = `translate(${cr.left + x * cr.w}px, ${cr.top + y * cr.h}px)`;

      // highlighter(s)
      const els = root.querySelectorAll<HTMLElement>("[data-hl]");
      if (!c) {
        els.forEach((e) => (e.style.opacity = "0"));
        return;
      }
      const on = landed.current;
      c.lines.forEach((L, i) => {
        const lineEl = root.querySelector<HTMLElement>(`[data-hl="${i}"]`);
        if (!lineEl) return;
        const pad = 6;
        const lw = (L.x1 - L.x0) * cr.w;
        lineEl.style.transform = `translate(${cr.left + L.x0 * cr.w - pad}px, ${cr.top + L.y0 * cr.h - 2}px)`;
        lineEl.style.width = `${lw + pad * 2}px`;
        lineEl.style.height = `${(L.y1 - L.y0) * cr.h + 4}px`;
        lineEl.style.opacity = on ? "1" : "0";
        const lp = clamp((progress - c.cum[i]) / Math.max(1e-6, c.cum[i + 1] - c.cum[i]), 0, 1);
        const fill = lineEl.querySelector<HTMLElement>(".hl-fill");
        if (fill) fill.style.width = `${lp * lw + (lp > 0 ? pad : 0)}px`;
      });
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [imgRef]);

  return (
    <div ref={layer} className="cursor-layer" aria-hidden style={{ display: visible ? "block" : "none" }}>
      {lines.map((_, i) => (
        <div key={`${pointer?.key}-${i}`} data-hl={i} className="hl-line">
          <div className="hl-fill" />
        </div>
      ))}
      <div ref={arrow} className="cursor-arrow" style={{ opacity: started.current || pointer ? 1 : 0 }}>
        <svg width="26" height="30" viewBox="0 0 26 30">
          <path d="M2 2 L2 24 L8 18.5 L12.5 28 L17 26 L12.6 16.8 L21 16.5 Z" fill="#fff" stroke="#111" strokeWidth="1.8" strokeLinejoin="round" />
        </svg>
      </div>
    </div>
  );
}
