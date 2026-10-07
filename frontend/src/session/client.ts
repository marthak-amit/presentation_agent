import { PlaybackQueue, QueueItem } from "../audio/playbackQueue";
import { StreamingPlayer } from "../audio/streamingPlayer";
import { startMic, MicHandle } from "../audio/mic";
import { initialSnapshot, PState, ServerMsg, Snapshot } from "./types";

/** Owns the WebSocket, playback queue and mic for one presenter page. */
export class PresenterClient {
  snap: Snapshot = { ...initialSnapshot };
  private ws: WebSocket | null = null;
  private queue: PlaybackQueue;
  private player: StreamingPlayer;
  private mic: MicHandle | null = null;
  private listeners = new Set<() => void>();
  private closed = false;
  private retry = 0;
  private reconnectTimer = 0;
  private lastPos = { slide: 0, sentence: 0 };
  private wasPresenting = false;

  constructor(private deckId: string) {
    this.queue = new PlaybackQueue({
      onStart: (item) => this.onItemStart(item),
      onEnd: (item, interrupted) => this.send({ type: "audio_ended", play_id: item.playId, interrupted }),
    });
    this.player = new StreamingPlayer(this.queue);
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private set(patch: Partial<Snapshot>): void {
    this.snap = { ...this.snap, ...patch };
    this.listeners.forEach((l) => l());
  }

  connect(): void {
    this.closed = false;
    this.detach();
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/session`);
    this.ws = ws;
    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.retry = 0;
      this.set({ connected: true, error: "" });
      const resume = this.lastPos.slide > 0 ? { slide_n: this.lastPos.slide, sentence_i: this.lastPos.sentence } : {};
      this.send({ type: "start_session", deck_id: this.deckId, barge_in: this.snap.bargeIn, ...resume });
    };
    ws.onmessage = (e) => {
      if (this.ws !== ws) return;
      try {
        this.onMessage(JSON.parse(e.data as string) as ServerMsg);
      } catch (err) {
        console.error("bad server message", err);
      }
    };
    ws.onclose = () => {
      if (this.ws !== ws) return; // stale socket (StrictMode remount / replaced connection)
      this.set({ connected: false, ready: false });
      if (this.closed) return;
      // Server session is gone: keep the local queue playing out, then resume where we were.
      this.wasPresenting = this.snap.state === "PRESENTING" || this.wasPresenting;
      const delay = Math.min(4000, 400 * 2 ** this.retry++);
      this.reconnectTimer = window.setTimeout(() => this.connect(), delay);
    };
    ws.onerror = () => ws.close();
  }

  private detach(): void {
    const old = this.ws;
    this.ws = null;
    window.clearTimeout(this.reconnectTimer);
    if (old) {
      old.onopen = old.onmessage = old.onclose = old.onerror = null;
      old.close();
    }
  }

  close(): void {
    this.closed = true;
    this.stopMic();
    this.detach();
    void this.queue.fadeOutAndClear(50);
  }

  send(obj: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj));
  }

  // ---- commands
  async start(): Promise<void> {
    await this.queue.unlock();
    this.send({ type: "control", action: "start" });
  }
  control(action: "pause" | "resume" | "next" | "prev" | "restart"): void {
    void this.queue.unlock();
    this.send({ type: "control", action: action });
  }
  goto(n: number): void {
    this.send({ type: "control", action: "goto", slide_n: n });
  }
  handRaise(): void {
    void this.queue.unlock();
    this.send({ type: "hand_raise" });
  }
  ptt(active: boolean): void {
    this.send({ type: "ptt", active });
  }
  setBargeIn(enabled: boolean): void {
    this.set({ bargeIn: enabled });
    this.send({ type: "set_barge_in", enabled });
  }
  simulate(text: string, utteranceEnd = true): void {
    this.send({ type: "sim_transcript", text, is_final: true, confidence: 0.95, utterance_end: utteranceEnd });
  }
  async enableMic(): Promise<void> {
    if (this.mic) return;
    try {
      this.mic = await startMic((pcm) => {
        if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(pcm);
      });
      this.set({ micOn: true, micError: "" });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      this.set({ micOn: false, micError: `Mic unavailable (${msg}). Voice commands need the mic; use the simulate box instead.` });
    }
  }
  stopMic(): void {
    this.mic?.stop();
    this.mic = null;
    this.set({ micOn: false });
  }

  // ---- inbound
  private onItemStart(item: QueueItem): void {
    const patch: Partial<Snapshot> = {
      prevCaption: this.snap.caption,
      caption: item.text,
      captionKind: item.kind,
    };
    if (item.kind === "sentence" && item.slideN !== undefined) {
      patch.slideN = item.slideN;
      patch.temporarySlide = false;
      this.lastPos = { slide: item.slideN, sentence: item.sentenceI ?? 0 };
    }
    this.set(patch);
  }

  private onMessage(m: ServerMsg): void {
    switch (m.type) {
      case "session_ready":
        this.set({ ready: true, services: m.services as Record<string, string>, bargeIn: m.barge_in as boolean });
        if (this.wasPresenting) {
          this.wasPresenting = false;
          this.send({ type: "control", action: "start" });
        }
        break;
      case "state": {
        const state = m.state as PState;
        this.set({
          state,
          reason: m.reason as string,
          sttStatus: m.stt as string,
          warnings: (m.warnings as string[]) ?? [],
          bargeIn: m.barge_in as boolean,
          hand: state === "PAUSED" || state === "LISTENING" ? this.snap.hand : false,
        });
        if (state === "IDLE" || state === "LISTENING") this.set({ caption: "", prevCaption: "", captionKind: "", transcript: "" });
        break;
      }
      case "slide":
        this.set({ slideN: m.slide_n as number, temporarySlide: m.temporary as boolean });
        break;
      case "play_sentence":
        this.queue.enqueue({
          playId: m.play_id as string,
          kind: "sentence",
          text: m.text as string,
          url: m.url as string,
          slideN: m.slide_n as number,
          sentenceI: m.sentence_i as number,
        });
        if (m.next_url) this.queue.preload(m.next_url as string);
        break;
      case "play_clip":
        this.queue.enqueue({ playId: m.play_id as string, kind: "clip", text: m.text as string, url: m.url as string });
        break;
      case "play_answer":
        this.player.push(m.play_id as string, m.audio_b64 as string, m.text as string);
        break;
      case "pause":
        void this.queue.fadeOutAndClear((m.fade_ms as number) ?? 150);
        break;
      case "transcript":
        this.set({ transcript: m.text as string });
        break;
      case "barge_in_hit":
        this.set({ hand: true });
        break;
      case "model_info":
        this.set({ lastModel: m.model as string, lastFirstAudioMs: (m.first_audio_ms as number | null) ?? null });
        break;
      case "summary":
        this.set({
          questions: m.questions as Snapshot["questions"],
          unanswered: m.unanswered as Snapshot["unanswered"],
        });
        break;
      case "error":
        this.set({ error: m.message as string });
        break;
      default:
        break;
    }
  }
}
