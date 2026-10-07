import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, DeckInfo } from "../api";

// Phase 1: read-only viewer (slide + captions + prev/next). Phase 2 replaces this with the live presenter.
export default function Presenter() {
  const { deckId = "" } = useParams();
  const [deck, setDeck] = useState<DeckInfo | null>(null);
  const [idx, setIdx] = useState(0);
  useEffect(() => {
    void api.getDeck(deckId).then(setDeck);
  }, [deckId]);
  if (!deck) return <main className="page">Loading…</main>;
  const slide = deck.slides[idx];
  return (
    <main className="page">
      <div className="stage">
        <img className="slide" src={slide.image_url} alt={slide.title} />
      </div>
      <div className="captions">{slide.sentences.join(" ")}</div>
      <div className="row">
        <button onClick={() => setIdx(Math.max(0, idx - 1))} disabled={idx === 0}>
          Prev
        </button>
        <span>
          {idx + 1} / {deck.slides.length}
        </span>
        <button onClick={() => setIdx(Math.min(deck.slides.length - 1, idx + 1))} disabled={idx === deck.slides.length - 1}>
          Next
        </button>
      </div>
    </main>
  );
}
