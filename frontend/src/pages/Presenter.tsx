import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Link, useParams } from "react-router-dom";
import { api, DeckInfo } from "../api";
import { PresenterClient } from "../session/client";
import SlideCursor from "../components/SlideCursor";

export default function Presenter() {
  const { deckId = "" } = useParams();
  const [deck, setDeck] = useState<DeckInfo | null>(null);
  const [err, setErr] = useState("");
  const [simText, setSimText] = useState("");
  const imgRef = useRef<HTMLImageElement>(null);
  const rootRef = useRef<HTMLElement>(null);
  const [full, setFull] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const client = useMemo(() => new PresenterClient(deckId), [deckId]);
  const snap = useSyncExternalStore(
    (cb) => client.subscribe(cb),
    () => client.snap,
  );

  useEffect(() => {
    api.getDeck(deckId).then(setDeck).catch((e) => setErr(String(e)));
  }, [deckId]);
  useEffect(() => {
    client.connect();
    return () => client.close();
  }, [client]);

  useEffect(() => {
    const onFs = () => setFull(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, []);

  useEffect(() => {
    if (snap.startedAt === null) {
      setElapsed(0);
      return;
    }
    const t = window.setInterval(() => setElapsed(Math.floor((Date.now() - (snap.startedAt ?? Date.now())) / 1000)), 1000);
    return () => window.clearInterval(t);
  }, [snap.startedAt]);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void rootRef.current?.requestFullscreen?.();
  }, []);

  // keyboard control for live use: Space pause/resume, arrows prev/next, F fullscreen, M mic, B voice commands
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const st = client.snap.state;
      switch (e.key) {
        case " ":
          e.preventDefault();
          if (st === "IDLE") void client.start();
          else if (st === "PRESENTING") client.control("pause");
          else if (st === "PAUSED" || st === "LISTENING") client.control("resume");
          break;
        case "ArrowRight":
          client.control("next");
          break;
        case "ArrowLeft":
          client.control("prev");
          break;
        case "f":
        case "F":
          toggleFullscreen();
          break;
        case "m":
        case "M":
          if (client.snap.micOn) client.stopMic();
          else void client.enableMic();
          break;
        case "b":
        case "B":
          client.setBargeIn(!client.snap.bargeIn);
          break;
        default:
      }
    };
    let talking = false;
    const down = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if ((e.key === "t" || e.key === "T") && !e.repeat && !talking && !e.metaKey && !e.ctrlKey) {
        talking = true;
        client.ptt(true);
      }
    };
    const up = (e: KeyboardEvent) => {
      if ((e.key === "t" || e.key === "T") && talking) {
        talking = false;
        client.ptt(false);
      }
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [client, toggleFullscreen]);

  if (err) return <main className="page error">{err}</main>;
  if (!deck) return <main className="page">Loading…</main>;
  if (deck.status !== "ready") return <main className="page">Deck is still processing ({deck.stage}). <Link to="/">Back</Link></main>;

  const slide = deck.slides.find((s) => s.n === snap.slideN) ?? deck.slides[0];
  const idle = snap.state === "IDLE";
  const presenting = snap.state === "PRESENTING";
  const paused = snap.state === "PAUSED";
  const ended = snap.state === "END" || snap.state === "OPEN_QA";
  const listening = snap.state === "LISTENING" || (paused && snap.reason !== "user");
  const answering = snap.state === "ANSWERING";

  return (
    <main ref={rootRef} className={`page presenter-root${full ? " full" : ""}`} data-state={snap.state}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="row" style={{ margin: 0 }}>
          <span className={`badge ${snap.state}`} data-testid="state">{snap.state}</span>
          <span className="muted">
            Slide {slide.n}/{deck.slides.length}
            {snap.temporarySlide && " (shown for answer)"}
          </span>
          <span className="muted">
            <span className={`dot ${snap.connected ? "on" : ""}`} />
            {snap.connected ? "connected" : "reconnecting…"}
          </span>
          <span className="muted">
            mic: {snap.micOn ? "on" : "off"} · STT: {snap.services.stt ?? "?"} ({snap.sttStatus}) · TTS: {snap.services.tts ?? "?"}{snap.voiceProvider && <> (playing: <b style={{ color: snap.voiceProvider === snap.services.tts ? "var(--good)" : "var(--warn)" }}>{snap.voiceProvider}</b>)</>} · LLM:{" "}
            {snap.services.llm ?? "?"}
          </span>
        </div>
        <div className="row" style={{ margin: 0 }}>
          {snap.startedAt !== null && <span className="muted mono" data-testid="timer">⏱ {String(Math.floor(elapsed / 60)).padStart(2, "0")}:{String(elapsed % 60).padStart(2, "0")}</span>}
          <button onClick={toggleFullscreen} title="Fullscreen (F)" data-testid="fullscreen">{full ? "Exit full screen" : "⛶ Full screen"}</button>
          <Link to={`/script/${deckId}`}>Edit script</Link>
          <Link to={`/debug/${deckId}`} target="_blank">Debug ↗</Link>
        </div>
      </div>

      <div className="progress" title={`slide ${slide.n} of ${deck.slides.length}`}>
        <div style={{ width: `${(slide.n / deck.slides.length) * 100}%` }} />
      </div>
      {snap.notice && <div className="toast" key={snap.notice.key} data-testid="notice">{snap.notice.text}</div>}
      {snap.warnings.length > 0 && (
        <div className="warn" data-testid="warnings">
          {snap.warnings.map((w) => (
            <div key={w}>⚠ {w}</div>
          ))}
        </div>
      )}

      <div className="stage">
        <img ref={imgRef} className="slide" src={slide.image_url} alt={slide.title} data-testid="slide-img" />
        <SlideCursor imgRef={imgRef} pointer={snap.pointer} visible={!idle && !ended} />
        {listening && (
          <div className="listening" data-testid="listening">
            <span className="pulse" /> Listening… ask your question
            {snap.transcript && <div className="heard">“{snap.transcript}”</div>}
          </div>
        )}
        {answering && <div className="listening answering" data-testid="answering"><span className="pulse" /> Answering…</div>}
        {ended && (
          <div className="overlay" data-testid="end-screen">
            <h2>{snap.state === "OPEN_QA" ? "Open Q&A" : "That's the end"}</h2>
            <p className="muted">Say “Okay Agent” and ask your question (or use the simulate box).</p>
            <h3>Questions asked ({snap.questions.length})</h3>
            <ol>{snap.questions.map((q, i) => <li key={i}>{q.question}</li>)}</ol>
            <h3>Unanswered — follow up ({snap.unanswered.length})</h3>
            <ol>{snap.unanswered.map((q, i) => <li key={i}>{q.question}</li>)}</ol>
            <div className="row">
              <button onClick={() => client.control("restart")}>Present again</button>
              {snap.sessionId && snap.questions.length > 0 && (
                <a className="btn" href={`/sessions/${snap.sessionId}/export.md`} download data-testid="export">⬇ Download Q&amp;A report (.md)</a>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="captions" data-testid="captions">
        <div className="prev">{snap.prevCaption}</div>
        <div className="cur" style={{ fontStyle: snap.captionKind === "clip" ? "italic" : "normal" }}>{snap.caption || " "}</div>
        {snap.transcript && <div className="muted mono">🎤 {snap.transcript}</div>}
      </div>

      <div className="thumbs" data-testid="thumbs">
        {deck.slides.map((sl) => (
          <button key={sl.n} className={`thumb${sl.n === slide.n ? " cur" : ""}`} onClick={() => client.goto(sl.n)} title={`${sl.n}. ${sl.title}`}>
            <img src={sl.image_url} alt="" loading="lazy" />
            <span>{sl.n}</span>
          </button>
        ))}
      </div>

      <div className="row controls">
        {idle ? (
          <button className="primary" onClick={() => void client.start()} disabled={!snap.ready} data-testid="start">▶ Start presenting</button>
        ) : (
          <>
            <button onClick={() => client.control("prev")} disabled={!(presenting || paused)}>⏮ Prev</button>
            {presenting ? (
              <button onClick={() => client.control("pause")} data-testid="pause">⏸ Pause</button>
            ) : (
              <button onClick={() => client.control("resume")} disabled={snap.state !== "PAUSED" && snap.state !== "LISTENING"} data-testid="resume">▶ Resume</button>
            )}
            <button onClick={() => client.control("next")} disabled={!(presenting || paused)}>Next ⏭</button>
          </>
        )}
        <label className="row" style={{ margin: 0 }}>
          <input type="checkbox" checked={snap.bargeIn} onChange={(e) => client.setBargeIn(e.target.checked)} data-testid="bargein" />
          Voice barge-in {snap.bargeIn ? "ON" : "OFF (push-to-talk only)"}
        </label>
        {(
          <button
            title="Hold (or hold the T key) while you speak - works even in a noisy room"
            onMouseDown={() => client.ptt(true)}
            onMouseUp={() => client.ptt(false)}
            onMouseLeave={() => snap.state === "LISTENING" && client.ptt(false)}
            onTouchStart={() => client.ptt(true)}
            onTouchEnd={() => client.ptt(false)}
          >
            🎙 Hold to talk
          </button>
        )}
        <button onClick={() => (snap.micOn ? client.stopMic() : void client.enableMic())} data-testid="mic">
          {snap.micOn ? "Mic on — click to stop" : "Enable microphone"}
        </button>
      </div>
      <p className="muted" data-testid="wake-hint">
        🎤 To ask a question, say <b>“Okay Agent”</b> {snap.micOn ? "(listening…)" : "— enable the microphone first"}
        {!snap.bargeIn && " · voice commands are OFF: use Hold to talk"}
      </p>
      <details className="hint-box">
        <summary className="muted">Voice commands &amp; keyboard shortcuts</summary>
        <p className="muted">
          After <b>“Okay Agent”</b>: ask anything · “next slide” · “previous slide” · “go to slide 3” · “pause” · “continue” · “repeat this slide” · “start over”.
          While it answers: say “Okay Agent”, “wait” or “stop” to cut in.
        </p>
        <p className="muted">Keys: <kbd>Space</kbd> pause/resume · <kbd>←</kbd>/<kbd>→</kbd> slides · <kbd>F</kbd> full screen · <kbd>M</kbd> mic · <kbd>B</kbd> voice commands on/off · hold <kbd>T</kbd> to talk (push-to-talk)</p>
      </details>
      {snap.micError && <p className="error">{snap.micError}</p>}
      {snap.error && <p className="error">{snap.error}</p>}

      <details>
        <summary className="muted">Simulate speech (no mic / mock STT)</summary>
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            if (simText.trim()) client.simulate(simText.trim());
            setSimText("");
          }}
        >
          <input type="text" value={simText} onChange={(e) => setSimText(e.target.value)} placeholder='e.g. "I have a question about pricing"' data-testid="sim-input" />
          <button type="submit" data-testid="sim-send">Say it</button>
        </form>
      </details>
    </main>
  );
}
