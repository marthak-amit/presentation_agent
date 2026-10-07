import { RefObject, useEffect, useRef } from "react";
import { ActivePointer } from "../session/types";

/**
 * A fake mouse arrow that moves like a person: curved path, ease-in-out, tiny hand tremor, then glides along the
 * line while it is being spoken (so it "reads" with the voice). Coordinates are normalised to the slide image.
 */
type P = [number, number];
interface Phase {
  t0: number;
  dur: number;
  from: P;
  to: P;
  ctrl?: P;
  kind: "travel" | "sweep" | "hold";
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

export default function SlideCursor({
  imgRef,
  pointer,
  visible,
}: {
  imgRef: RefObject<HTMLImageElement>;
  pointer: ActivePointer | null;
  visible: boolean;
}) {
  const arrow = useRef<HTMLDivElement>(null);
  const hl = useRef<HTMLDivElement>(null);
  const pos = useRef<P>([0.97, 0.95]);
  const started = useRef(false);
  const phases = useRef<Phase[]>([]);
  const box = useRef<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const hlOn = useRef(false);
  const seed = useRef(Math.random() * 1000);

  // schedule a new move whenever the spoken target changes
  useEffect(() => {
    const img = imgRef.current;
    if (!pointer || !img) {
      box.current = null;
      hlOn.current = false;
      phases.current = [];
      return;
    }
    const cr = contentRect(img);
    const h = pointer.y1 - pointer.y0;
    const padX = Math.min(0.012, (pointer.x1 - pointer.x0) * 0.04);
    const start: P = [pointer.x0 + padX, pointer.y0 + h * 0.78];
    const end: P = [Math.max(start[0], pointer.x1 - padX), pointer.y0 + h * 0.78];
    const from = pos.current;
    const dx = (start[0] - from[0]) * cr.w;
    const dy = (start[1] - from[1]) * cr.h;
    const dist = Math.hypot(dx, dy);
    const now = performance.now();
    const travel = started.current ? clamp(520 + dist * 0.9, 560, 1400) : 1000;
    const bend = (Math.random() < 0.5 ? -1 : 1) * clamp(dist * 0.18, 8, 90); // curved, never a straight laser line
    const nx = dist ? -dy / dist : 0;
    const ny = dist ? dx / dist : 0;
    const ctrl: P = [
      (from[0] + start[0]) / 2 + (nx * bend) / cr.w,
      (from[1] + start[1]) / 2 + (ny * bend) / cr.h,
    ];
    const hold = 140;
    const sweep = clamp(pointer.durationMs - travel - hold - 200, 500, 14000);
    phases.current = [
      { t0: now, dur: travel, from, to: start, ctrl, kind: "travel" },
      { t0: now + travel, dur: hold, from: start, to: start, kind: "hold" },
      { t0: now + travel + hold, dur: sweep, from: start, to: end, kind: "sweep" },
    ];
    box.current = { x0: pointer.x0, y0: pointer.y0, x1: pointer.x1, y1: pointer.y1 };
    hlOn.current = false;
    started.current = true;
  }, [pointer?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  // one animation loop for the lifetime of the component
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const img = imgRef.current;
      const el = arrow.current;
      const hel = hl.current;
      if (!img || !el || !hel) return;
      const t = performance.now();
      const cr = contentRect(img);
      let [x, y] = pos.current;
      const ph = phases.current.find((p) => t >= p.t0 && t < p.t0 + p.dur) ?? null;
      const last = phases.current[phases.current.length - 1];
      if (ph) {
        const u = clamp((t - ph.t0) / ph.dur, 0, 1);
        if (ph.kind === "travel" && ph.ctrl) {
          const e = ease(u);
          const a = (1 - e) * (1 - e);
          const b = 2 * (1 - e) * e;
          const c = e * e;
          x = a * ph.from[0] + b * ph.ctrl[0] + c * ph.to[0];
          y = a * ph.from[1] + b * ph.ctrl[1] + c * ph.to[1];
        } else if (ph.kind === "sweep") {
          // mostly linear (matches the pace of speech) with slight human unevenness
          const e = 0.85 * u + 0.15 * (u * u * (3 - 2 * u));
          x = ph.from[0] + (ph.to[0] - ph.from[0]) * e;
          y = ph.from[1] + Math.sin(u * Math.PI * 5 + seed.current) * 0.0025;
        } else {
          x = ph.to[0];
          y = ph.to[1];
        }
        if (ph.kind !== "travel" && !hlOn.current) {
          hlOn.current = true;
          el.firstElementChild?.animate(
            [{ transform: "scale(1)" }, { transform: "scale(0.82)" }, { transform: "scale(1)" }],
            { duration: 220, easing: "ease-out" },
          ); // a "click" as it lands on the line
        }
        pos.current = [x, y];
      } else if (last && t >= last.t0 + last.dur) {
        x = last.to[0] + Math.sin(t / 900 + seed.current) * 0.0012; // resting hand tremor
        y = last.to[1] + Math.cos(t / 1100 + seed.current) * 0.0018;
        pos.current = [last.to[0], last.to[1]];
      }
      // arrow
      el.style.transform = `translate(${cr.left + x * cr.w}px, ${cr.top + y * cr.h}px)`;
      // highlighter under the line being read
      const b = box.current;
      if (b && hlOn.current) {
        const pad = 6;
        hel.style.transform = `translate(${cr.left + b.x0 * cr.w - pad}px, ${cr.top + b.y0 * cr.h - 2}px)`;
        hel.style.width = `${(b.x1 - b.x0) * cr.w + pad * 2}px`;
        hel.style.height = `${(b.y1 - b.y0) * cr.h + 4}px`;
        hel.style.opacity = "1";
      } else {
        hel.style.opacity = "0";
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [imgRef]);

  return (
    <div className="cursor-layer" aria-hidden style={{ display: visible ? "block" : "none" }}>
      <div ref={hl} className="cursor-highlight" />
      <div ref={arrow} className="cursor-arrow" style={{ opacity: started.current || pointer ? 1 : 0 }}>
        <svg width="26" height="30" viewBox="0 0 26 30">
          <path
            d="M2 2 L2 24 L8 18.5 L12.5 28 L17 26 L12.6 16.8 L21 16.5 Z"
            fill="#fff"
            stroke="#111"
            strokeWidth="1.8"
            strokeLinejoin="round"
          />
        </svg>
      </div>
    </div>
  );
}
