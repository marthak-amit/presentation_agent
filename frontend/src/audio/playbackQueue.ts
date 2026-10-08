export interface PointerWord {
  x0: number;
  x1: number;
  key: boolean;
}
export interface PointerLine {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  words: PointerWord[];
}
export interface Pointer {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  text?: string;
  lines?: PointerLine[];
}

export interface QueueItem {
  playId: string;
  kind: "sentence" | "clip" | "answer";
  text: string;
  url?: string;
  data?: ArrayBuffer;
  slideN?: number;
  sentenceI?: number;
  pointer?: Pointer | null;
  provider?: string;
}

type Handlers = {
  onStart: (item: QueueItem, durationMs: number) => void;
  onEnd: (item: QueueItem, interrupted: boolean) => void;
};

/** Sequential WebAudio playback queue with fade-out/clear (barge-in) and decode cache. */
export class PlaybackQueue {
  private ctx: AudioContext;
  private gain: GainNode;
  private analyser: AnalyserNode;
  private levelBuf: Uint8Array<ArrayBuffer>;
  private queue: QueueItem[] = [];
  private busy = false;
  private current: { item: QueueItem; src: AudioBufferSourceNode | null; finish: (interrupted: boolean) => void } | null = null;
  private fading: Promise<void> | null = null;
  private cache = new Map<string, Promise<AudioBuffer | null>>();
  private gen = 0;

  constructor(private h: Handlers) {
    const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    this.ctx = new AC();
    this.gain = this.ctx.createGain();
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 512;
    this.levelBuf = new Uint8Array(new ArrayBuffer(this.analyser.fftSize));
    this.gain.connect(this.analyser);
    this.analyser.connect(this.ctx.destination);
  }

  /** Current output loudness 0..1 (drives the voice orb). */
  level(): number {
    this.analyser.getByteTimeDomainData(this.levelBuf);
    let peak = 0;
    for (let i = 0; i < this.levelBuf.length; i++) peak = Math.max(peak, Math.abs(this.levelBuf[i] - 128));
    return Math.min(1, peak / 70);
  }

  /** Must be called from a user gesture (Start button) to satisfy autoplay policy. */
  async unlock(): Promise<void> {
    if (this.ctx.state !== "running") await this.ctx.resume();
  }

  get pending(): number {
    return this.queue.length + (this.current ? 1 : 0);
  }

  preload(url: string): void {
    void this.decodeUrl(url);
  }

  enqueue(item: QueueItem): void {
    this.queue.push(item);
    void this.pump();
  }

  /** Fade the current clip out over `ms`, then drop everything queued. */
  fadeOutAndClear(ms: number): Promise<void> {
    this.gen++;
    this.queue = [];
    const cur = this.current;
    if (!cur) return Promise.resolve();
    const now = this.ctx.currentTime;
    this.gain.gain.cancelScheduledValues(now);
    this.gain.gain.setValueAtTime(this.gain.gain.value, now);
    this.gain.gain.linearRampToValueAtTime(0, now + ms / 1000);
    this.fading = new Promise<void>((resolve) => {
      window.setTimeout(() => {
        try {
          cur.src?.stop();
        } catch {
          /* already stopped */
        }
        cur.finish(true);
        this.gain.gain.cancelScheduledValues(this.ctx.currentTime);
        this.gain.gain.setValueAtTime(1, this.ctx.currentTime);
        this.fading = null;
        resolve();
      }, ms + 10);
    });
    return this.fading;
  }

  private decodeUrl(url: string): Promise<AudioBuffer | null> {
    let p = this.cache.get(url);
    if (!p) {
      p = fetch(url)
        .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(String(r.status)))))
        .then((b) => this.ctx.decodeAudioData(b))
        .catch(() => null);
      this.cache.set(url, p);
      if (this.cache.size > 80) this.cache.delete(this.cache.keys().next().value as string);
    }
    return p;
  }

  private async load(item: QueueItem): Promise<AudioBuffer | null> {
    try {
      if (item.data) return await this.ctx.decodeAudioData(item.data.slice(0));
      if (item.url) return await this.decodeUrl(item.url);
    } catch {
      /* fall through */
    }
    return null;
  }

  private async pump(): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    try {
      while (this.queue.length) {
        if (this.fading) await this.fading;
        const item = this.queue.shift();
        if (!item) break;
        const gen = this.gen;
        const buf = await this.load(item);
        if (gen !== this.gen) continue; // cleared while decoding
        if (this.ctx.state !== "running") await this.ctx.resume().catch(() => undefined);
        await new Promise<void>((resolve) => {
          let done = false;
          let timer = 0;
          const finish = (interrupted: boolean) => {
            if (done) return;
            done = true;
            window.clearTimeout(timer);
            this.current = null;
            this.h.onEnd(item, interrupted);
            resolve();
          };
          let src: AudioBufferSourceNode | null = null;
          if (buf) {
            src = this.ctx.createBufferSource();
            src.buffer = buf;
            src.connect(this.gain);
            src.onended = () => finish(false);
            // safety net: some browsers never fire onended for tiny/silent buffers
            timer = window.setTimeout(() => finish(false), buf.duration * 1000 + 400);
          } else {
            // decode failed: keep the show moving using a text-length estimate
            timer = window.setTimeout(() => finish(false), Math.max(700, item.text.split(/\s+/).length * 360));
          }
          this.current = { item, src, finish };
          this.h.onStart(item, buf ? buf.duration * 1000 : Math.max(700, item.text.split(/\s+/).length * 360));
          src?.start();
        });
      }
    } finally {
      this.busy = false;
    }
  }
}
