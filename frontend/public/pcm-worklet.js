// AudioWorklet: float32 @ device rate -> PCM16 mono @ 16 kHz, posted in ~100 ms chunks.
class Pcm16Downsampler extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.target = (options.processorOptions && options.processorOptions.targetRate) || 16000;
    this.ratio = sampleRate / this.target;
    this.pos = 0;
    this.acc = 0;
    this.n = 0;
    this.out = new Int16Array(1600);
    this.outLen = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.acc += ch[i];
      this.n++;
      this.pos += 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        const v = Math.max(-1, Math.min(1, this.acc / this.n));
        this.out[this.outLen++] = v < 0 ? v * 0x8000 : v * 0x7fff;
        this.acc = 0;
        this.n = 0;
        if (this.outLen === this.out.length) {
          const buf = this.out.buffer.slice(0);
          this.port.postMessage(buf, [buf]);
          this.outLen = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("pcm16-downsampler", Pcm16Downsampler);
