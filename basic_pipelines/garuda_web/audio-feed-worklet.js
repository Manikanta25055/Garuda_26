// Plays the camera's microphone (/ws/audio) with as little delay as the
// network allows. app.js posts Float32 chunks; this keeps a small buffer
// (targetMs) against network jitter and never lets it grow: a little above
// target it plays 4% fast to catch up, far above (maxMs) it drops the oldest
// sound. An empty buffer plays silence until the target is refilled.
class FeedPlayer extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = options.processorOptions || {};
    const inRate = o.inputRate || sampleRate;
    this.step = inRate / sampleRate;            // input samples per output sample
    this.target = Math.round(inRate * (o.targetMs || 60) / 1000);
    this.ease = Math.round(inRate * 40 / 1000);
    this.max = Math.round(inRate * (o.maxMs || 180) / 1000);
    this.buf = new Float32Array(inRate * 2);    // 2 s ring, far more than is ever kept
    this.r = 0; this.w = 0; this.count = 0; this.frac = 0;
    this.playing = false;
    this.reportAt = 0;
    this.port.onmessage = (e) => this.push(e.data);
  }

  push(samples) {
    const n = this.buf.length;
    for (let i = 0; i < samples.length; i++) {
      this.buf[this.w] = samples[i];
      this.w = (this.w + 1) % n;
    }
    this.count += samples.length;
    if (this.count > this.max) {               // a burst after a stall: skip to live
      const drop = this.count - this.target;
      this.r = (this.r + drop) % n;
      this.count -= drop;
    }
  }

  process(_inputs, outputs) {
    const out = outputs[0];
    const ch0 = out[0];
    const n = this.buf.length;
    if (!this.playing && this.count >= this.target) this.playing = true;
    let i = 0;
    if (this.playing) {
      const step = this.step * (this.count > this.target + this.ease ? 1.04 : 1);
      for (; i < ch0.length; i++) {
        if (this.count < 2) { this.playing = false; break; }
        const a = this.buf[this.r], b = this.buf[(this.r + 1) % n];
        ch0[i] = a + (b - a) * this.frac;
        this.frac += step;
        while (this.frac >= 1) { this.frac -= 1; this.r = (this.r + 1) % n; this.count--; }
      }
    }
    for (; i < ch0.length; i++) ch0[i] = 0;
    for (let c = 1; c < out.length; c++) out[c].set(ch0);
    if (currentTime >= this.reportAt) {          // buffered ms, for the status line
      this.reportAt = currentTime + 1;
      this.port.postMessage({ bufferedMs: Math.round(this.count / (this.step * sampleRate) * 1000) });
    }
    return true;
  }
}

registerProcessor('feed-player', FeedPlayer);
