import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

interface Ev {
  session_id: string;
  ts: number;
  event: { type: string; [k: string]: unknown };
}
interface Hit {
  ts: number;
  trigger: string;
  text: string;
  ok: boolean;
  detail: string;
}
interface Answer {
  ts: number;
  question: string;
  model: string;
  first_token_ms: number | null;
  first_audio_ms: number | null;
  total_ms: number | null;
  fallback: boolean;
}

const fmt = (n: number | null | undefined) => (n === null || n === undefined ? "–" : `${Math.round(n)} ms`);

export default function Debug() {
  const { deckId } = useParams();
  const [connected, setConnected] = useState(false);
  const [sessionId, setSessionId] = useState("");
  const [state, setState] = useState("–");
  const [stt, setStt] = useState("–");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [interim, setInterim] = useState({ text: "", final: false, conf: 0 });
  const [hits, setHits] = useState<Hit[]>([]);
  const [answers, setAnswers] = useState<Answer[]>([]);
  const [services, setServices] = useState<Record<string, string>>({});
  const [log, setLog] = useState<string[]>([]);
  const [text, setText] = useState("");
  const ws = useRef<WebSocket | null>(null);

  useEffect(() => {
    let closed = false;
    let timer = 0;
    const open = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const sock = new WebSocket(`${proto}://${location.host}/ws/debug`);
      ws.current = sock;
      sock.onopen = () => setConnected(true);
      sock.onclose = () => {
        setConnected(false);
        if (!closed) timer = window.setTimeout(open, 1000);
      };
      sock.onmessage = (m) => {
        const ev = JSON.parse(m.data as string) as Ev;
        const e = ev.event;
        setSessionId(ev.session_id);
        const line = `${new Date(ev.ts * 1000).toLocaleTimeString()}  ${e.type}  ${JSON.stringify(e).slice(0, 140)}`;
        if (e.type !== "transcript") setLog((l) => [...l.slice(-150), line]);
        switch (e.type) {
          case "state":
            setState(e.state as string);
            setStt(e.stt as string);
            setWarnings((e.warnings as string[]) ?? []);
            break;
          case "session_ready":
            setServices(e.services as Record<string, string>);
            setHits([]);
            setAnswers([]);
            break;
          case "transcript":
            setInterim({ text: e.text as string, final: e.is_final as boolean, conf: e.confidence as number });
            break;
          case "barge_in_hit":
            setHits((h) => [
              { ts: ev.ts, trigger: e.trigger as string, text: e.text as string, ok: true, detail: `${e.source} · score ${e.score} · detect ${e.detect_ms} ms` },
              ...h,
            ]);
            break;
          case "barge_in_ignored":
            setHits((h) => [{ ts: ev.ts, trigger: e.trigger as string, text: e.text as string, ok: false, detail: `ignored: ${e.reason}` }, ...h]);
            break;
          case "model_info":
            if (e.total_ms !== null && e.total_ms !== undefined)
              setAnswers((a) => [
                {
                  ts: ev.ts,
                  question: e.question as string,
                  model: e.model as string,
                  first_token_ms: e.first_token_ms as number | null,
                  first_audio_ms: e.first_audio_ms as number | null,
                  total_ms: e.total_ms as number | null,
                  fallback: e.fallback_used as boolean,
                },
                ...a,
              ]);
            break;
        }
      };
    };
    open();
    return () => {
      closed = true;
      window.clearTimeout(timer);
      ws.current?.close();
    };
  }, []);

  const last = answers[0];
  return (
    <main className="page">
      <h2>
        Debug {deckId ? <span className="muted">· deck {deckId}</span> : null}{" "}
        <span className="muted">
          <span className={`dot ${connected ? "on" : ""}`} />
          {connected ? `feed connected${sessionId ? ` · session ${sessionId}` : " · waiting for a session"}` : "connecting…"}
        </span>
      </h2>
      {warnings.map((w) => (
        <div className="warn" key={w}>⚠ {w}</div>
      ))}

      <div className="grid2">
        <section>
          <h3>Live transcript</h3>
          <div className={`interim ${interim.final ? "final" : ""}`} data-testid="interim">
            {interim.text || <span className="muted">(nothing yet)</span>}
          </div>
          <p className="muted mono">{interim.final ? "final" : "interim"} · confidence {interim.conf.toFixed(2)}</p>

          <h3>Trigger hits</h3>
          <table>
            <thead><tr><th>time</th><th>trigger</th><th>heard</th><th>result</th></tr></thead>
            <tbody>
              {hits.map((h, i) => (
                <tr key={i} style={{ color: h.ok ? "var(--good)" : "var(--warn)" }}>
                  <td className="mono">{new Date(h.ts * 1000).toLocaleTimeString()}</td>
                  <td>{h.trigger}</td>
                  <td>{h.text || "–"}</td>
                  <td>{h.detail}</td>
                </tr>
              ))}
              {hits.length === 0 && <tr><td colSpan={4} className="muted">none yet</td></tr>}
            </tbody>
          </table>

          <h3>Answers</h3>
          <table data-testid="answers">
            <thead><tr><th>question</th><th>model</th><th>first token</th><th>first audio</th><th>total</th></tr></thead>
            <tbody>
              {answers.map((a, i) => (
                <tr key={i}>
                  <td>{a.question}</td>
                  <td>{a.model}{a.fallback ? " (fallback)" : ""}</td>
                  <td>{fmt(a.first_token_ms)}</td>
                  <td>{fmt(a.first_audio_ms)}</td>
                  <td>{fmt(a.total_ms)}</td>
                </tr>
              ))}
              {answers.length === 0 && <tr><td colSpan={5} className="muted">none yet</td></tr>}
            </tbody>
          </table>
        </section>

        <section>
          <h3>Now</h3>
          <div className="kv">
            <div>State</div><div><span className={`badge ${state}`} data-testid="dbg-state">{state}</span></div>
            <div>Model used</div><div className="big">{last?.model || "–"}</div>
            <div>First token</div><div className="big">{fmt(last?.first_token_ms)}</div>
            <div>First audio</div><div className="big">{fmt(last?.first_audio_ms)}</div>
            <div>Total latency</div><div className="big">{fmt(last?.total_ms)}</div>
            <div>STT</div><div>{services.stt ?? "–"} ({stt})</div>
            <div>TTS</div><div>{services.tts ?? "–"}</div>
            <div>LLM</div><div>{services.llm ?? "–"}</div>
          </div>
          <h3>Simulate speech</h3>
          <form
            className="row"
            onSubmit={(e) => {
              e.preventDefault();
              if (text.trim()) ws.current?.send(JSON.stringify({ type: "sim_transcript", session_id: sessionId, text: text.trim(), is_final: true, utterance_end: true }));
              setText("");
            }}
          >
            <input type="text" value={text} onChange={(e) => setText(e.target.value)} placeholder="routes to the active session" />
            <button type="submit">Send</button>
          </form>
        </section>
      </div>

      <h3>Event log</h3>
      <div className="ev mono">{log.slice().reverse().map((l, i) => <div key={i}>{l}</div>)}</div>
    </main>
  );
}
