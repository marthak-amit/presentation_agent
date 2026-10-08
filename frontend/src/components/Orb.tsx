import { useEffect, useRef } from "react";

export type OrbMode = "idle" | "speaking" | "listening" | "thinking";

const LABEL: Record<OrbMode, string> = { idle: "", speaking: "Speaking", listening: "Listening…", thinking: "Thinking…" };

/** The agent's "face": breathes when idle, pulses with its voice when speaking, with the mic level when listening. */
export default function Orb({ mode, levels, size = 72 }: { mode: OrbMode; levels: () => { out: number; mic: number }; size?: number }) {
  const core = useRef<HTMLDivElement>(null);
  const ring = useRef<HTMLDivElement>(null);
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const smooth = useRef(0);

  useEffect(() => {
    let raf = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const m = modeRef.current;
      const l = levels();
      const target = m === "speaking" ? l.out : m === "listening" ? l.mic : 0;
      smooth.current += (target - smooth.current) * (target > smooth.current ? 0.5 : 0.12); // quick attack, slow release
      const v = smooth.current;
      if (core.current) core.current.style.transform = `scale(${1 + v * 0.42})`;
      if (ring.current) {
        ring.current.style.transform = `scale(${1.12 + v * 0.75})`;
        ring.current.style.opacity = String(m === "idle" ? 0.15 : 0.25 + v * 0.6);
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [levels]);

  return (
    <div className={`orb orb-${mode}`} style={{ width: size, height: size }} data-testid="orb" data-mode={mode} title={LABEL[mode] || "Ready"}>
      <div ref={ring} className="orb-ring" />
      <div ref={core} className="orb-core" />
      {mode === "thinking" && <div className="orb-spin" />}
    </div>
  );
}

export { LABEL as ORB_LABEL };
