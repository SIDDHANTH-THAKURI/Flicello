// Cello's ears: the microphone at 16 kHz mono 16-bit PCM in 40 ms chunks (what Gemini Live listens to), each with its
// loudness (RMS), averaging the input's own rate down by boxes (no aliasing worth hearing at speech rates).
class CelloMic extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.acc = 0;
    this.sum = 0;
    this.n = 0;
    this.out = new Int16Array(640);
    this.k = 0;
    this.sq = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i += 1) {
      this.sum += ch[i];
      this.n += 1;
      this.acc += 1;
      if (this.acc >= this.ratio) {
        const v = Math.max(-1, Math.min(1, this.sum / this.n));
        this.out[this.k] = v < 0 ? v * 0x8000 : v * 0x7fff;
        this.sq += v * v;
        this.k += 1;
        this.acc -= this.ratio;
        this.sum = 0;
        this.n = 0;
        if (this.k === this.out.length) {
          const chunk = this.out.slice(0);
          this.port.postMessage({ pcm: chunk.buffer, rms: Math.sqrt(this.sq / this.k) }, [chunk.buffer]);
          this.k = 0;
          this.sq = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor('cello-mic', CelloMic);
