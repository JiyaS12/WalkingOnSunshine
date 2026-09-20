// Local amplitude-based turn detection. This detects pauses, not semantic endings.
class TurnDetector {
  constructor({ silenceMs = 1500, minSpeechMs = 120 } = {}) {
    this.silenceMs = silenceMs;
    this.minSpeechMs = minSpeechMs;
    this.noiseFloor = 0.003;
    this.voicedSince = null;
    this.lastVoice = null;
    this.hasSpeech = false;
  }

  update(rms, now) {
    const speaking = rms >= Math.max(0.012, this.noiseFloor * 3);
    if (speaking) {
      if (this.voicedSince === null) this.voicedSince = now;
      this.lastVoice = now;
      if (now - this.voicedSince >= this.minSpeechMs) this.hasSpeech = true;
    } else {
      this.voicedSince = null;
      this.noiseFloor = this.noiseFloor * 0.95 + rms * 0.05;
    }
    return {
      speaking,
      hasSpeech: this.hasSpeech,
      complete: this.hasSpeech && !speaking && now - this.lastVoice >= this.silenceMs,
    };
  }
}

if (typeof module !== "undefined") module.exports = { TurnDetector };
