/**
 * PCM Processor — AudioWorkletProcessor
 *
 * Receives float32 audio frames from the Web Audio pipeline at the device
 * sample rate, downsamples to 16 000 Hz using linear interpolation, converts
 * to 16-bit signed integers (linear16), and posts ~100 ms buffers to the main
 * thread as transferable ArrayBuffers.
 *
 * The main thread (app.js) forwards each buffer over the streaming WebSocket
 * as a binary frame for the Deepgram / AssemblyAI real-time API.
 */

const TARGET_SAMPLE_RATE = 16000;
// ~100 ms worth of output samples before flushing
const FLUSH_THRESHOLD = TARGET_SAMPLE_RATE / 10; // 1600 samples

class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = [];
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0] || input[0].length === 0) return true;

    const src = input[0]; // float32, length = 128 frames at device rate
    // sampleRate is a global available inside AudioWorkletProcessor
    const ratio = sampleRate / TARGET_SAMPLE_RATE;
    const outLen = Math.floor(src.length / ratio);

    for (let i = 0; i < outLen; i++) {
      // Nearest-neighbour resample (sufficient for speech)
      const srcIdx = Math.min(Math.round(i * ratio), src.length - 1);
      const f = src[srcIdx];
      // float32 [-1, 1] → int16 [-32768, 32767], clipped
      const s = Math.max(-1, Math.min(1, f));
      this._buffer.push(s < 0 ? s * 32768 : s * 32767);
    }

    if (this._buffer.length >= FLUSH_THRESHOLD) {
      const int16 = new Int16Array(this._buffer);
      this._buffer = [];
      // Transfer the underlying ArrayBuffer (zero-copy)
      this.port.postMessage(int16.buffer, [int16.buffer]);
    }

    return true; // keep processor alive
  }
}

registerProcessor("pcm-processor", PCMProcessor);
