import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, DeckInfo, SlideInfo } from "../api";

const WPS = 2.6; // spoken words per second (≈155 wpm)

function secs(text: string): number {
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  return Math.round(words / WPS);
}

interface Row {
  text: string;
  base: string;
  instruction: string;
  busy: "" | "save" | "regen";
  msg: string;
}

export default function Script() {
  const { deckId = "" } = useParams();
  const [deck, setDeck] = useState<DeckInfo | null>(null);
  const [rows, setRows] = useState<Record<number, Row>>({});
  const [err, setErr] = useState("");
  const timer = useRef<number | undefined>(undefined);
  const [tone, setTone] = useState("conversational");
  const [length, setLength] = useState("standard");
  const [allBusy, setAllBusy] = useState(false);
  const [allMsg, setAllMsg] = useState("");

  const load = useCallback(async () => {
    const d = await api.getDeck(deckId);
    setDeck(d);
    setRows((prev) => {
      const next = { ...prev };
      for (const s of d.slides) {
        const text = s.sentences.join(" ");
        if (!next[s.n]) next[s.n] = { text, base: text, instruction: "", busy: "", msg: "" };
      }
      return next;
    });
    return d;
  }, [deckId]);

  useEffect(() => {
    void load().catch((e) => setErr(String(e)));
    return () => window.clearTimeout(timer.current);
  }, [load]);

  // poll audio generation progress after edits
  const watchAudio = useCallback(() => {
    window.clearTimeout(timer.current);
    const tick = async () => {
      try {
        const d = await api.getDeck(deckId);
        setDeck(d);
        const pending = d.slides.some((s) => (s.audio_urls ?? []).some((u) => !u));
        if (pending) timer.current = window.setTimeout(() => void tick(), 1200);
      } catch {
        /* ignore */
      }
    };
    void tick();
  }, [deckId]);

  const toneInit = useRef(false);
  useEffect(() => {
    if (deck && !toneInit.current) {
      toneInit.current = true;
      setTone(deck.tone ?? "conversational");
      setLength(deck.length ?? "standard");
    }
  }, [deck]);

  async function rewriteEverything() {
    if (!window.confirm("Rewrite the script of EVERY slide in this tone/length? Your manual edits will be replaced.")) return;
    setAllBusy(true);
    setAllMsg("Starting…");
    try {
      await api.rewriteAll(deckId, tone, length);
      const poll = async () => {
        const d = await api.getDeck(deckId);
        setDeck(d);
        const rw = d.rewrite;
        if (rw?.running) {
          setAllMsg(`Rewriting… ${rw.done}/${rw.total} slides`);
          timer.current = window.setTimeout(() => void poll(), 1500);
        } else {
          setAllBusy(false);
          setRows({});
          await load();
          setAllMsg(rw?.error ? `⚠ ${rw.error}` : "Done - all slides rewritten; the voice is generating…");
          watchAudio();
        }
      };
      await poll();
    } catch (e) {
      setAllBusy(false);
      setAllMsg(`⚠ ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  const patch = (n: number, p: Partial<Row>) => setRows((r) => ({ ...r, [n]: { ...r[n], ...p } }));

  async function save(s: SlideInfo) {
    const r = rows[s.n];
    patch(s.n, { busy: "save", msg: "" });
    try {
      const out = await api.saveScript(deckId, s.n, r.text);
      const text = out.sentences.join(" ");
      patch(s.n, { text, base: text, busy: "", msg: `Saved · ${out.sentences.length} sentences · voice is generating…` });
      watchAudio();
    } catch (e) {
      patch(s.n, { busy: "", msg: `⚠ ${e instanceof Error ? e.message : String(e)}` });
    }
  }

  async function regen(s: SlideInfo, instruction: string) {
    patch(s.n, { busy: "regen", msg: "" });
    try {
      const out = await api.regenerateScript(deckId, s.n, instruction);
      const text = out.sentences.join(" ");
      patch(s.n, { text, base: text, busy: "", instruction: "", msg: "Rewritten and saved · voice is generating…" });
      watchAudio();
    } catch (e) {
      patch(s.n, { busy: "", msg: `⚠ ${e instanceof Error ? e.message : String(e)}` });
    }
  }

  if (err) return <main className="page error">{err}</main>;
  if (!deck) return <main className="page">Loading…</main>;
  const total = Object.values(rows).reduce((a, r) => a + secs(r.text), 0);
  const pending = deck.slides.flatMap((s) => s.audio_urls ?? []).filter((u) => !u).length;

  return (
    <main className="page">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Script · {deck.name}</h2>
        <div className="row" style={{ margin: 0 }}>
          <span className="muted">
            ≈ {Math.floor(total / 60)} min {total % 60} s total{pending > 0 ? ` · ${pending} sentence(s) of audio generating…` : " · all audio ready"}
          </span>
          <Link to={`/present/${deckId}`}><button className="primary">Present ▶</button></Link>
        </div>
      </div>
      <p className="muted">
        This is exactly what will be said. Edit freely: changes are saved per slide, new sentences are voiced automatically, and the answer
        index and slide highlighter follow. Open sessions pick up edits when you reload the presenter page.
      </p>
      <section className="card">
        <b>Style for the whole deck</b>
        <div className="row">
          <label className="muted">Tone{" "}
            <select value={tone} onChange={(e) => setTone(e.target.value)} data-testid="script-tone">
              <option value="conversational">Conversational</option>
              <option value="formal">Formal</option>
              <option value="energetic">Energetic</option>
              <option value="storytelling">Storytelling</option>
            </select>
          </label>
          <label className="muted">Length per slide{" "}
            <select value={length} onChange={(e) => setLength(e.target.value)} data-testid="script-length">
              <option value="short">Short (~30-45 s)</option>
              <option value="standard">Standard (~45-75 s)</option>
              <option value="long">Long (~80-100 s)</option>
            </select>
          </label>
          <button onClick={() => void rewriteEverything()} disabled={allBusy} data-testid="rewrite-all">{allBusy ? "Rewriting…" : "Rewrite ALL slides"}</button>
          <span className={allMsg.startsWith("⚠") ? "error" : "muted"}>{allMsg}</span>
        </div>
        <p className="muted" style={{ margin: 0 }}>
          Tone and length are chosen when a deck is uploaded; use this to change them on a deck that already exists. Rewriting uses your speaker notes as the source again.
        </p>
      </section>
      {deck.slides.map((s) => {
        const r = rows[s.n];
        if (!r) return null;
        const dirty = r.text !== r.base;
        return (
          <section key={s.n} className={`slide-edit${r.msg.startsWith("Saved") || r.msg.startsWith("Rewritten") ? " saved" : ""}`} data-testid={`slide-${s.n}`}>
            <div>
              <img src={s.image_url} alt={s.title} />
              <div className="muted" style={{ marginTop: 6 }}>{s.n}. {s.title}</div>
            </div>
            <div>
              <textarea rows={Math.max(5, Math.ceil(r.text.length / 90))} value={r.text} onChange={(e) => patch(s.n, { text: e.target.value, msg: "" })} data-testid={`text-${s.n}`} />
              <div className="row">
                <button className="primary" onClick={() => void save(s)} disabled={!dirty || r.busy !== ""} data-testid={`save-${s.n}`}>
                  {r.busy === "save" ? "Saving…" : "Save"}
                </button>
                {dirty && <button onClick={() => patch(s.n, { text: r.base })}>Revert</button>}
                <span className="muted">{r.text.trim() ? r.text.trim().split(/\s+/).length : 0} words · ≈ {secs(r.text)} s</span>
                <span className={r.msg.startsWith("⚠") ? "error" : "muted"}>{r.msg}</span>
              </div>
              <div className="row">
                <input type="text" placeholder="Ask the AI to rewrite it: e.g. “shorter”, “more casual”, “mention the pilot results”" value={r.instruction} onChange={(e) => patch(s.n, { instruction: e.target.value })} />
                <button onClick={() => void regen(s, r.instruction)} disabled={r.busy !== ""} data-testid={`regen-${s.n}`}>{r.busy === "regen" ? "Writing…" : "Rewrite"}</button>
                {["Shorter", "More casual", "Add an example"].map((q) => (
                  <button key={q} className="chip" onClick={() => void regen(s, q.toLowerCase())} disabled={r.busy !== ""}>{q}</button>
                ))}
              </div>
            </div>
          </section>
        );
      })}
    </main>
  );
}
