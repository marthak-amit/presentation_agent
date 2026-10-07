import { useCallback, useEffect, useRef, useState } from "react";
import { startMic, MicHandle } from "../audio/mic";

interface CheckRow {
  id: string;
  label: string;
  status: "ok" | "warn" | "fail" | "mock";
  detail: string;
  fix: string;
  ms: number | null;
}
interface Report {
  overall: "ok" | "warn" | "fail";
  presenter: string;
  checks: CheckRow[];
}

const ICON: Record<string, string> = { ok: "✅", warn: "⚠️", fail: "❌", mock: "🧪" };

export default function Check() {
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [level, setLevel] = useState(0);
  const [micState, setMicState] = useState<"off" | "on" | "error">("off");
  const [micMsg, setMicMsg] = useState("");
  const [peak, setPeak] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [voiceNote, setVoiceNote] = useState<{ ok: boolean; text: string } | null>(null);
  const stopMic = useRef<(() => void) | null>(null);
  const [sttOn, setSttOn] = useState(false);
  const [sttHeard, setSttHeard] = useState<{ text: string; final: boolean; wake: boolean } | null>(null);
  const [sttWake, setSttWake] = useState(false);
  const [sttMsg, setSttMsg] = useState("");
  const sttRef = useRef<{ ws: WebSocket; mic: MicHandle | null } | null>(null);

  const run = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const r = await fetch("/preflight");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setReport((await r.json()) as Report);
    } catch (e) {
      setErr(`Cannot reach the backend: ${String(e)}. Is "make dev" running?`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void run();
    return () => {
      stopMic.current?.();
      sttRef.current?.mic?.stop();
      sttRef.current?.ws.close();
    };
  }, [run]);

  async function toggleStt() {
    if (sttOn && sttRef.current) {
      sttRef.current.mic?.stop();
      sttRef.current.ws.close();
      sttRef.current = null;
      setSttOn(false);
      return;
    }
    setSttHeard(null);
    setSttWake(false);
    setSttMsg("Connecting…");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/stt-test`);
    ws.binaryType = "arraybuffer";
    const handle: { ws: WebSocket; mic: MicHandle | null } = { ws, mic: null };
    sttRef.current = handle;
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data as string);
      if (m.type === "mock") setSttMsg(m.message);
      else if (m.type === "status") setSttMsg(m.status === "up" ? "Listening - say “Okay Agent”…" : m.error || `speech recognition ${m.status}`);
      else if (m.type === "transcript") {
        setSttHeard({ text: m.text, final: m.is_final, wake: m.wake });
        if (m.wake) setSttWake(true);
      }
    };
    ws.onclose = () => setSttOn(false);
    ws.onopen = async () => {
      try {
        handle.mic = await startMic((pcm) => {
          if (ws.readyState === WebSocket.OPEN) ws.send(pcm);
        });
        setSttOn(true);
      } catch (err) {
        setSttMsg(`Microphone: ${err instanceof Error ? err.message : String(err)}`);
        ws.close();
      }
    };
  }

  async function toggleMic() {
    if (micState === "on") {
      stopMic.current?.();
      setMicState("off");
      setLevel(0);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new AC();
      const an = ctx.createAnalyser();
      an.fftSize = 1024;
      ctx.createMediaStreamSource(stream).connect(an);
      const buf = new Uint8Array(an.fftSize);
      let raf = 0;
      let pk = 0;
      const loop = () => {
        an.getByteTimeDomainData(buf);
        let m = 0;
        for (const v of buf) m = Math.max(m, Math.abs(v - 128));
        const lv = Math.min(1, m / 60);
        pk = Math.max(pk * 0.995, lv);
        setLevel(lv);
        setPeak(pk);
        raf = requestAnimationFrame(loop);
      };
      loop();
      const track = stream.getAudioTracks()[0];
      setMicMsg(`${track.label || "microphone"}`);
      setMicState("on");
      stopMic.current = () => {
        cancelAnimationFrame(raf);
        stream.getTracks().forEach((t) => t.stop());
        void ctx.close();
      };
    } catch (e) {
      setMicState("error");
      setMicMsg(`${e instanceof Error ? e.message : String(e)} - allow the microphone in the browser's site settings`);
    }
  }

  async function playVoice() {
    setPlaying(true);
    try {
      const r = await fetch("/preflight/voice");
      const provider = r.headers.get("X-TTS-Provider") ?? "?";
      const primary = r.headers.get("X-TTS-Primary") ?? "";
      const why = r.headers.get("X-TTS-Error") ?? "";
      setVoiceNote(
        primary && provider !== primary
          ? { ok: false, text: `⚠ This is NOT your ${primary} voice - it fell back to "${provider}". Reason: ${why || "unknown"}` }
          : { ok: provider !== "fake", text: provider === "fake" ? "Silent mock audio (no TTS keys)." : `Played with ${provider} ✔` },
      );
      const blob = await r.blob();
      const a = new Audio(URL.createObjectURL(blob));
      a.onended = () => setPlaying(false);
      a.onerror = () => setPlaying(false);
      await a.play();
      void a;
    } catch (e) {
      setErr(`Could not play the voice test: ${String(e)}`);
      setPlaying(false);
    }
  }

  return (
    <main className="page narrow">
      <h1>Setup check</h1>
      <p className="muted">
        Verifies keys, models and tools with real requests, so you find problems before the demo.
        {report && <> Presenter name: <b>{report.presenter}</b>.</>}
      </p>
      <div className="row">
        <button className="primary" onClick={() => void run()} disabled={loading} data-testid="recheck">
          {loading ? "Checking…" : "Run checks again"}
        </button>
        {report && (
          <span className={`badge ${report.overall === "fail" ? "PAUSED" : report.overall === "ok" ? "PRESENTING" : "LISTENING"}`} data-testid="overall">
            {report.overall === "ok" ? "READY" : report.overall === "warn" ? "READY (with notes)" : "FIX REQUIRED"}
          </span>
        )}
      </div>
      {err && <p className="error">{err}</p>}

      <section className="card" data-testid="checks">
        {report?.checks.map((c) => (
          <div key={c.id} className="check-row" data-status={c.status}>
            <div className="check-icon">{ICON[c.status]}</div>
            <div>
              <div>
                <b>{c.label}</b> {c.ms !== null && <span className="muted mono">{c.ms} ms</span>}
              </div>
              <div className="muted">{c.detail}</div>
              {c.fix && c.status !== "ok" && <div className="fix">→ {c.fix}</div>}
            </div>
          </div>
        ))}
        {!report && !err && <p className="muted">Running checks…</p>}
      </section>

      <section className="card">
        <h3>Try it yourself</h3>
        <div className="row">
          <button onClick={() => void toggleMic()} data-testid="mic-test">{micState === "on" ? "Stop microphone test" : "Test microphone"}</button>
          <div className="meter" title="mic level"><div style={{ width: `${Math.round(level * 100)}%` }} /></div>
          <span className="muted">{micState === "on" ? (peak > 0.15 ? "I hear you ✔" : "speak now…") : ""}</span>
        </div>
        {micMsg && <p className={micState === "error" ? "error" : "muted"}>{micMsg}</p>}
        <div className="row">
          <button onClick={() => void playVoice()} disabled={playing} data-testid="voice-test">{playing ? "Playing…" : "Play voice test"}</button>
          <span className="muted">Plays a sentence in the configured voice.</span>
        </div>
        {voiceNote && <p className={voiceNote.ok ? "good" : "error"} data-testid="voice-note">{voiceNote.text}</p>}
        <div className="row">
          <button onClick={() => void toggleStt()} data-testid="stt-test">{sttOn ? "Stop speech test" : "Test speech recognition + wake phrase"}</button>
          <span className={sttWake ? "good" : "muted"}>{sttWake ? "✅ “Okay Agent” heard!" : sttMsg}</span>
        </div>
        {sttHeard && <div className={`interim ${sttHeard.final ? "final" : ""}`} data-testid="stt-heard">{sttHeard.text}</div>}
        <p className="muted">
          Tip: wear headphones for the live demo. Then say <b>“Okay Agent”</b> during a slide to ask a question.
        </p>
      </section>
    </main>
  );
}
