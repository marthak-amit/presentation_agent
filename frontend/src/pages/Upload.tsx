import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, DeckInfo, DeckSummary } from "../api";

export default function Upload() {
  const nav = useNavigate();
  const [deck, setDeck] = useState<DeckInfo | null>(null);
  const [decks, setDecks] = useState<DeckSummary[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [docMsg, setDocMsg] = useState("");
  const [drag, setDrag] = useState(false);
  const [tone, setTone] = useState("conversational");
  const [length, setLength] = useState("standard");
  const timer = useRef<number | undefined>(undefined);

  const refreshList = useCallback(() => api.listDecks().then(setDecks).catch(() => undefined), []);
  useEffect(() => {
    void refreshList();
    return () => window.clearTimeout(timer.current);
  }, [refreshList]);

  const poll = useCallback(
    async (id: string) => {
      try {
        const d = await api.getDeck(id);
        setDeck(d);
        if (d.status === "processing") timer.current = window.setTimeout(() => void poll(id), 1000);
        else void refreshList();
      } catch (e) {
        setError(String(e));
      }
    },
    [refreshList],
  );

  async function onFile(f: File | undefined) {
    if (!f) return;
    setError("");
    setDeck(null);
    setBusy(true);
    try {
      const { deck_id } = await api.uploadDeck(f, tone, length);
      await poll(deck_id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDocs(files: FileList | null) {
    if (!files || !deck) return;
    setDocMsg("Indexing…");
    try {
      const r = await api.addDocs(deck.deck_id, Array.from(files));
      setDocMsg(`Added ${r.chunks} chunks to the knowledge base.`);
      setDeck(await api.getDeck(deck.deck_id));
    } catch (e) {
      setDocMsg(String(e));
    }
  }

  const ready = deck?.status === "ready";
  return (
    <main className="page narrow">
      <h1>Upload a deck</h1>
      <p className="muted">
        Upload a .pptx. We render the slides, write a first-person narration from your speaker notes, and index everything for live Q&amp;A.
      </p>
      <div className="row">
        <label className="muted">
          Tone{" "}
          <select value={tone} onChange={(e) => setTone(e.target.value)} data-testid="tone">
            <option value="conversational">Conversational</option>
            <option value="formal">Formal</option>
            <option value="energetic">Energetic</option>
            <option value="storytelling">Storytelling</option>
          </select>
        </label>
        <label className="muted">
          Length per slide{" "}
          <select value={length} onChange={(e) => setLength(e.target.value)} data-testid="length">
            <option value="short">Short (~30-45 s)</option>
            <option value="standard">Standard (~45-75 s)</option>
            <option value="long">Long (~80-100 s)</option>
          </select>
        </label>
        <Link to="/check" className="muted">Check setup ↗</Link>
      </div>
      <label
        className={`drop ${drag ? "drag" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          void onFile(e.dataTransfer.files[0]);
        }}
      >
        <input type="file" accept=".pptx" hidden onChange={(e) => void onFile(e.target.files?.[0])} data-testid="deck-input" />
        {busy ? "Uploading…" : "Drop a .pptx here or click to choose"}
      </label>
      {error && <p className="error">{error}</p>}

      {deck && (
        <section className="card">
          <h3>{deck.name}</h3>
          <div className="bar">
            <div style={{ width: `${Math.round(deck.progress * 100)}%` }} />
          </div>
          <p className="muted">
            {deck.status === "error" ? `Error: ${deck.error}` : deck.status === "ready" ? "Ready" : `Processing: ${deck.stage}`}
            {ready && deck.audio.total > 0 && ` · audio ${deck.audio.done}/${deck.audio.total}`}
          </p>
          {ready && (
            <>
              <div className="row">
                <button className="primary" onClick={() => nav(`/present/${deck.deck_id}`)}>
                  Open presenter
                </button>
                <button onClick={() => nav(`/script/${deck.deck_id}`)}>Edit script</button>
                <label className="btn">
                  Add reference docs (pdf/txt/md)
                  <input type="file" multiple accept=".pdf,.txt,.md" hidden onChange={(e) => void onDocs(e.target.files)} />
                </label>
              </div>
              {deck.audio.providers && Object.keys(deck.audio.providers).length > 0 && (
                <p className="muted">Voice: {Object.entries(deck.audio.providers).map(([k, v]) => `${k} ×${v}`).join(", ")}</p>
              )}
              {((deck.audio.fake ?? 0) > 0 || (deck.audio.other_voice ?? 0) > 0) && (
                <p className="warn">
                  ⚠ {(deck.audio.fake ?? 0) + (deck.audio.other_voice ?? 0)} sentence(s) are NOT in your configured voice (the voice service failed, so a fallback voice or silence was used).{" "}
                  <button onClick={() => void api.retryAudio(deck.deck_id).then(() => void poll(deck.deck_id))}>Retry voice generation</button>
                </p>
              )}
              {docMsg && <p className="muted">{docMsg}</p>}
              {deck.docs.length > 0 && <p className="muted">Docs: {deck.docs.map((d) => d.name).join(", ")}</p>}
            </>
          )}
        </section>
      )}

      {decks.length > 0 && (
        <section>
          <h3>Previous decks</h3>
          <ul className="decklist">
            {decks.map((d) => (
              <li key={d.deck_id}>
                <span>{d.name}</span>
                <span className="muted">{d.status}</span>
                <span className="row" style={{ margin: 0 }}>
                  {d.status === "ready" && <button onClick={() => nav(`/present/${d.deck_id}`)}>Present</button>}
                  {d.status === "ready" && <button onClick={() => nav(`/script/${d.deck_id}`)}>Script</button>}
                  <button
                    className="danger-ghost"
                    title="Delete this deck"
                    onClick={() => {
                      if (window.confirm(`Delete "${d.name}" and its audio?`)) void api.deleteDeck(d.deck_id).then(refreshList);
                    }}
                  >
                    🗑
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
