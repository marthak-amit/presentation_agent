export interface MicHandle {
  stop: () => void;
}

/** Streams mic audio as 16 kHz mono PCM16 chunks (~100 ms) via an AudioWorklet. Echo cancellation ON. */
export async function startMic(onChunk: (pcm: ArrayBuffer) => void, onLevel?: (rms: number) => void): Promise<MicHandle> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  });
  const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const ctx = new AC();
  await ctx.audioWorklet.addModule("/pcm-worklet.js");
  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "pcm16-downsampler", { processorOptions: { targetRate: 16000 } });
  node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
    if (onLevel) {
      const a = new Int16Array(e.data);
      let sum = 0;
      for (let i = 0; i < a.length; i += 8) sum += a[i] * a[i];
      onLevel(Math.min(1, Math.sqrt(sum / (a.length / 8)) / 5000));
    }
    onChunk(e.data);
  };
  src.connect(node); // not connected to destination: no monitoring/echo
  return {
    stop: () => {
      node.disconnect();
      src.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      void ctx.close();
    },
  };
}
