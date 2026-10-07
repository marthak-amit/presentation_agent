export interface SlideInfo {
  n: number;
  image_url: string;
  title: string;
  sentences: string[];
  audio_urls?: (string | null)[];
}
export interface DeckInfo {
  deck_id: string;
  name: string;
  status: "processing" | "ready" | "error" | string;
  stage: string;
  progress: number;
  error: string | null;
  audio: { done: number; total: number; fake?: number };
  docs: { name: string; chunks: number }[];
  tone?: string;
  length?: string;
  slides: SlideInfo[];
}
export interface DeckSummary {
  deck_id: string;
  name: string;
  status: string;
  stage: string;
  progress: number;
  slide_count?: number;
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = r.statusText;
    try {
      msg = (await r.json()).detail ?? msg;
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export const api = {
  async uploadDeck(file: File, tone = "conversational", length = "standard"): Promise<{ deck_id: string }> {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("tone", tone);
    fd.append("length", length);
    return j(await fetch("/decks", { method: "POST", body: fd }));
  },
  async addDocs(deckId: string, files: File[]): Promise<{ chunks: number }> {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    return j(await fetch(`/decks/${deckId}/docs`, { method: "POST", body: fd }));
  },
  async getDeck(deckId: string): Promise<DeckInfo> {
    return j(await fetch(`/decks/${deckId}`));
  },
  async saveScript(deckId: string, n: number, text: string): Promise<SlideInfo> {
    return j(
      await fetch(`/decks/${deckId}/narration/${n}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      }),
    );
  },
  async regenerateScript(deckId: string, n: number, instruction: string): Promise<SlideInfo> {
    return j(
      await fetch(`/decks/${deckId}/narration/${n}/regenerate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction }),
      }),
    );
  },
  async retryAudio(deckId: string): Promise<void> {
    await j(await fetch(`/decks/${deckId}/audio`, { method: "POST" }));
  },
  async deleteDeck(deckId: string): Promise<void> {
    await j(await fetch(`/decks/${deckId}`, { method: "DELETE" }));
  },
  async listDecks(): Promise<DeckSummary[]> {
    return j(await fetch("/decks"));
  },
};
