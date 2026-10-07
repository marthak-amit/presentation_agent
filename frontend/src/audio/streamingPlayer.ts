import { PlaybackQueue, Pointer } from "./playbackQueue";

function b64ToBuffer(b64: string): ArrayBuffer {
  const bin = atob(b64);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf.buffer;
}

/** Receives answer sentences (base64 MP3) as they are synthesised and feeds the playback queue immediately. */
export class StreamingPlayer {
  constructor(private queue: PlaybackQueue) {}

  push(playId: string, b64: string, text: string, pointer?: Pointer | null, slideN?: number | null): void {
    this.queue.enqueue({ playId, kind: "answer", text, data: b64ToBuffer(b64), pointer, slideN: slideN ?? undefined });
  }
}
