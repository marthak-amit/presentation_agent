import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
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
    <main className="page" data-state={snap.state}>
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
            mic: {snap.micOn ? "on" : "off"} · STT: {snap.services.stt ?? "?"} ({snap.sttStatus}) · TTS: {snap.services.tts ?? "?"} · LLM:{" "}
            {snap.services.llm ?? "?"}
          </span>
        </div>
        <Link to={`/debug/${deckId}`} target="_blank">Debug ↗</Link>
      </div>

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
            <button onClick={() => client.control("restart")}>Present again</button>
          </div>
        )}
      </div>

      <div className="captions" data-testid="captions">
        <div className="prev">{snap.prevCaption}</div>
        <div className="cur" style={{ fontStyle: snap.captionKind === "clip" ? "italic" : "normal" }}>{snap.caption || " "}</div>
        {snap.transcript && <div className="muted mono">🎤 {snap.transcript}</div>}
      </div>

      <div className="row">
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
        {!snap.bargeIn && (
          <button
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
