// Tap PCM separately from the unmodified playback route.
class PCVAudioTap extends AudioWorkletProcessor {
  constructor() {
    super();
    this.block = new Float32Array(4096);
    this.offset = 0;
  }

  process(inputs) {
    const channels = inputs[0];
    if (!channels?.length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      let mixed = 0;
      for (const channel of channels) mixed += channel[i];
      this.block[this.offset++] = mixed / channels.length;
      if (this.offset === this.block.length) {
        const end = currentFrame + i + 1;
        this.port.postMessage({samples: this.block, startFrame: end - this.block.length}, [this.block.buffer]);
        this.block = new Float32Array(4096);
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor('pcv-audio-tap', PCVAudioTap);
