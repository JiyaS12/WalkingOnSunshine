// Run with: node --test tests/test_voice_ui.cjs
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { TurnDetector } = require("../voice_web/voice_activity.js");

function setup({ microphoneError, speechError, responseState = "awaiting_confirmation", uploadError } = {}) {
  const mediaRequests = [];
  class Element {
    constructor() { this.listeners = {}; this.children = []; this.paused = true; this.value = "RGN-0417"; }
    addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
    async emit(type, data = {}) { for (const fn of this.listeners[type] || []) await fn(data); }
    appendChild(child) { this.children.push(child); }
    replaceChildren() { this.children = []; }
    scrollIntoView() {}
    removeAttribute(name) { delete this[name]; }
    load() { this.ended = false; }
    pause() { this.paused = true; this.emit("pause"); }
    async play() {
      mediaRequests.push(this.src);
      if (speechError) throw new Error("Speech stream failed");
      this.paused = false;
      await this.emit("play");
    }
    async end() { this.paused = true; this.ended = true; await this.emit("pause"); await this.emit("ended"); }
  }
  const elements = Object.fromEntries(["conversation", "record", "listen-toggle", "status", "start", "voice", "patient", "token"].map(id => [id, new Element()]));
  const calls = [];
  const blobs = [];
  const revoked = [];
  const storage = new Map([["operatorToken", "synthetic-operator-token"]]);
  let microphoneCalls = 0;
  let stoppedTracks = 0;
  let latestRecorder;
  let now = 0;
  let amplitude = 0;
  const timers = new Map();
  const windowEvents = {};
  let nextTimer = 0;
  const tracks = [];
  class AudioContext {
    async resume() {}
    async close() {}
    createMediaStreamSource() { return { connect() {} }; }
    createAnalyser() { return { getFloatTimeDomainData: samples => samples.fill(amplitude) }; }
  }
  class Recorder extends Element {
    static isTypeSupported(type) { return type === "audio/webm;codecs=opus"; }
    constructor(stream, options) { super(); this.mimeType = options.mimeType; this.state = "inactive"; latestRecorder = this; }
    start() { this.state = "recording"; }
    stop() {
      this.state = "inactive";
      this.stopped = this.emit("dataavailable", { data: new Blob(["synthetic-audio"]) }).then(() => this.emit("stop"));
    }
  }
  const context = {
    document: { querySelector: selector => elements[selector.slice(1)], createElement: () => new Element() },
    window: { MediaRecorder: Recorder, AudioContext, speechSynthesis: { cancel() {} }, addEventListener: (name, fn) => { windowEvents[name] = fn; } },
    sessionStorage: {
      getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
    },
    navigator: { mediaDevices: { getUserMedia: async () => {
      microphoneCalls++;
      if (microphoneError) throw microphoneError;
      const track = { enabled: true, stop: () => stoppedTracks++ };
      tracks.push(track);
      return { getTracks: () => [track] };
    } } },
    fetch: async (url, options) => {
      calls.push({ url, options });
      if (url.includes("/speech?")) {
        return new Response("synthetic speech", { headers: { "Content-Type": "audio/mpeg" } });
      }
      if (url.endsWith("/audio")) {
        if (uploadError) return new Response(JSON.stringify({ detail: "Transcription unavailable" }), { status: 502 });
        return new Response(JSON.stringify({
          transcript: "Mild.", prompt: "Is mild right?", prompt_id: "turn-2", state: responseState,
        }));
      }
      return new Response(JSON.stringify({ session_id: "session-1", prompt: "First question.", prompt_id: "turn-1", state: "asking" }));
    },
    MediaRecorder: Recorder, Blob, FormData, Date, encodeURIComponent, TurnDetector,
    performance: { now: () => now },
    URL: {
      createObjectURL: blob => {
        blobs.push(blob);
        return `blob:synthetic-voice-${blobs.length}`;
      },
      revokeObjectURL: url => revoked.push(url),
    },
    setInterval: fn => { timers.set(++nextTimer, fn); return nextTimer; },
    clearInterval: id => timers.delete(id),
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../voice_web/app.js"), "utf8"), context);
  return { elements, calls, mediaRequests, tracks, windowEvents, blobs, revoked, storage,
    tick(duration, rms = 0) {
      amplitude = rms;
      for (let time = 0; time < duration; time += 50) {
        now += 50;
        for (const fn of [...timers.values()]) fn();
      }
    },
    get recorder() { return latestRecorder; },
    get microphoneCalls() { return microphoneCalls; }, get stoppedTracks() { return stoppedTracks; } };
}

test("hands-free speech and silence upload a turn and automatically listen again", async () => {
  const app = setup();
  const { elements: e } = app;
  assert.equal(e.token.value, "synthetic-operator-token");
  e.token.value = "  synthetic-current-token  ";
  await e.start.emit("click");
  assert.equal(app.mediaRequests[0], "blob:synthetic-voice-1");
  assert.ok(app.calls.some(call => call.url === "/api/sessions/session-1/speech?prompt_id=turn-1"));
  assert.equal(app.blobs[0].type, "audio/mpeg");
  assert.equal(await app.blobs[0].text(), "synthetic speech");
  assert.equal(e.voice.preload, "auto");
  assert.equal(e.record.disabled, true);
  assert.equal(app.recorder, undefined);
  assert.equal(app.tracks[0].enabled, false);
  await e.voice.end();
  assert.equal(e.record.disabled, false);
  assert.equal(app.microphoneCalls, 1);
  assert.equal(app.recorder.state, "recording");
  assert.equal(app.tracks[0].enabled, true);
  assert.match(e.status.textContent, /Listening/);
  assert.equal(e.start.disabled, true);
  app.tick(400, 0.08);
  app.tick(1000, 0);
  assert.equal(app.recorder.state, "recording");
  app.tick(300, 0.08); // Speech resumes during the allowed thinking pause.
  app.tick(1500, 0);
  await app.recorder.stopped;
  assert.equal(app.stoppedTracks, 0);
  assert.equal(app.tracks[0].enabled, false);
  assert.ok(e.conversation.children.some(row => row.textContent === "You: Mild."));
  assert.deepEqual(app.mediaRequests, ["blob:synthetic-voice-1", "blob:synthetic-voice-2"]);
  assert.ok(app.calls.some(call => call.url === "/api/sessions/session-1/speech?prompt_id=turn-2"));
  assert.deepEqual(app.revoked, ["blob:synthetic-voice-1"]);
  assert.equal(app.storage.get("operatorToken"), "synthetic-current-token");
  assert.equal(app.calls.length, 4);
  for (const call of app.calls) {
    assert.equal(call.options.headers.Authorization, "Bearer synthetic-current-token");
    assert.ok(!call.url.includes("token"));
  }
  const upload = app.calls.find(call => call.url.endsWith("/audio"));
  assert.equal(upload.options.body.get("audio").type, "audio/webm;codecs=opus");
  await e.voice.end();
  assert.equal(e.record.disabled, false);
  assert.equal(app.recorder.state, "recording");
  assert.equal(app.microphoneCalls, 1);
});

test("microphone denial is visible and permits retry", async () => {
  const error = new Error("Permission denied");
  error.name = "NotAllowedError";
  const { elements: e, calls } = setup({ microphoneError: error });
  await e.start.emit("click");
  assert.match(e.status.textContent, /Microphone permission was denied/);
  assert.equal(e.record.disabled, true);
  assert.equal(e.start.disabled, false);
  assert.equal(calls.some(call => call.url.endsWith("/audio")), false);
});

test("speech failure preserves the question and allows resuming without browser TTS", async () => {
  const app = setup({ speechError: true });
  const { elements: e } = app;
  await e.start.emit("click");
  assert.match(e.status.textContent, /voice could not play/);
  assert.equal(e.conversation.children[0].textContent, "Survey: First question.");
  assert.equal(e.record.disabled, true);
  await e["listen-toggle"].emit("click");
  assert.equal(app.recorder.state, "recording");
});

test("silence and isolated clicks never submit or consume a survey attempt", async () => {
  const app = setup();
  await app.elements.start.emit("click");
  await app.elements.voice.end();
  app.tick(50, 0.08);
  app.tick(65000, 0.001);
  assert.equal(app.calls.filter(call => call.url.endsWith("/audio")).length, 0);
  assert.equal(app.recorder.state, "recording");
});

test("mic pause discards partial audio, releases tracks, and resumes explicitly", async () => {
  const app = setup();
  const e = app.elements;
  await e.start.emit("click");
  await e.voice.end();
  app.tick(400, 0.08);
  await e["listen-toggle"].emit("click");
  await app.recorder.stopped;
  assert.equal(app.stoppedTracks, 1);
  app.tick(2000);
  assert.equal(app.calls.filter(call => call.url.endsWith("/audio")).length, 0);
  assert.equal(e["listen-toggle"].textContent, "Resume listening");
  await e["listen-toggle"].emit("click");
  assert.equal(app.microphoneCalls, 2);
  assert.equal(app.recorder.state, "recording");
  await e.record.emit("click"); // Optional manual override still works.
  await app.recorder.stopped;
  assert.equal(app.calls.filter(call => call.url.endsWith("/audio")).length, 1);
});

test("completion releases the mic and does not start another recording", async () => {
  const app = setup({ responseState: "complete" });
  const e = app.elements;
  await e.start.emit("click");
  await e.voice.end();
  app.tick(250, 0.08); // A short spoken yes is enough.
  app.tick(1500);
  await app.recorder.stopped;
  await e.voice.end();
  assert.equal(app.stoppedTracks, 1);
  assert.equal(app.recorder.state, "inactive");
  assert.equal(e.record.disabled, true);
  assert.equal(e.start.disabled, false);
});

test("an upload failure stops automatic retries and allows manual recovery", async () => {
  const app = setup({ uploadError: true });
  await app.elements.start.emit("click");
  await app.elements.voice.end();
  app.tick(300, 0.08);
  app.tick(1500);
  await app.recorder.stopped;
  assert.match(app.elements.status.textContent, /Transcription unavailable/);
  assert.equal(app.stoppedTracks, 1);
  app.tick(6000);
  assert.equal(app.calls.filter(call => call.url.endsWith("/audio")).length, 1);
  await app.elements["listen-toggle"].emit("click");
  assert.equal(app.recorder.state, "recording");
});

test("replaying the helper discards captured input and disables the mic during playback", async () => {
  const app = setup();
  await app.elements.start.emit("click");
  await app.elements.voice.end();
  app.tick(300, 0.08);
  await app.elements.voice.play();
  await app.recorder.stopped;
  assert.equal(app.tracks[0].enabled, false);
  assert.equal(app.calls.filter(call => call.url.endsWith("/audio")).length, 0);
  await app.elements.voice.end();
  assert.equal(app.recorder.state, "recording");
  app.windowEvents.pagehide();
  assert.equal(app.stoppedTracks, 1);
});

test("the detector waits for sustained speech, then a full silence interval", () => {
  const detector = new TurnDetector();
  assert.equal(detector.update(0, 10000).complete, false);
  detector.update(0.05, 10050);
  detector.update(0.05, 10200);
  assert.equal(detector.update(0, 11699).complete, false);
  assert.equal(detector.update(0, 11700).complete, true);
});
