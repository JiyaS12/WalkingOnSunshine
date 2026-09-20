const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function setup({ unauthorized = false, speechFailure = false } = {}) {
  class Element {
    constructor() { this.listeners = {}; this.children = []; this.value = ""; this.paused = true; }
    addEventListener(name, fn) { (this.listeners[name] ??= []).push(fn); }
    async emit(name) { for (const fn of this.listeners[name] || []) await fn(); }
    appendChild(child) { this.children.push(child); }
    replaceChildren() { this.children = []; }
    scrollIntoView() {}
    removeAttribute(name) { delete this[name]; }
    load() {}
    pause() { this.paused = true; }
    async play() { this.paused = false; await this.emit("play"); }
  }
  const elements = Object.fromEntries(
    ["conversation", "record", "listen-toggle", "status", "start", "voice", "patient", "token"]
      .map(name => [name, new Element()]),
  );
  elements.patient.value = "RGN-0417";
  const calls = [], revoked = [], blobs = [];
  const track = { enabled: true, stopped: false, stop() { this.stopped = true; } };
  const storage = new Map([["operatorToken", "synthetic-operator-token"]]);
  class AudioContext {
    async resume() {}
    async close() {}
    createAnalyser() { return {}; }
    createMediaStreamSource() { return { connect() {} }; }
  }
  const context = vm.createContext({
    document: { querySelector: selector => elements[selector.slice(1)], createElement: () => new Element() },
    window: { AudioContext, MediaRecorder: class {}, addEventListener() {} },
    navigator: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [track] }) } },
    sessionStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    fetch: async (url, options) => {
      calls.push({ url, options });
      if (unauthorized) return new Response(JSON.stringify({ detail: "Operator token missing or incorrect." }), { status: 401 });
      if (url.includes("/speech?")) return new Response("synthetic audio", { status: speechFailure ? 503 : 200 });
      return new Response(JSON.stringify({
        session_id: "session-1", prompt_id: "prompt-1",
        prompt: "Synthetic question", state: "asking", transcript: "mild",
      }));
    },
    URL: {
      createObjectURL: blob => { blobs.push(blob); return `blob:voice-${blobs.length}`; },
      revokeObjectURL: url => revoked.push(url),
    },
    Blob, FormData, encodeURIComponent,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../voice_web/app.js"), "utf8"), context);
  return { elements, calls, track, storage, revoked, blobs, context };
}

test("operator credentials authenticate session, speech and audio; never enter media URLs", async () => {
  const app = setup();
  app.elements.token.value = "  synthetic-current-token  ";
  await app.elements.start.emit("click");
  assert.equal(app.elements.voice.src, "blob:voice-1");
  assert.equal(app.track.enabled, false);
  await app.context.sendRecording(new Blob(["synthetic reply"]), "session-1");
  assert.equal(app.calls.length, 4);
  assert.ok(app.calls.some(call => call.url.endsWith("/audio")));
  for (const call of app.calls) {
    assert.equal(call.options.headers.Authorization, "Bearer synthetic-current-token");
    assert.ok(!call.url.includes("token"));
  }
  assert.equal(app.storage.get("operatorToken"), "synthetic-current-token");
  assert.deepEqual(app.revoked, ["blob:voice-1"]);
  assert.equal(app.blobs.length, 2);
});

test("rejected operator authentication never requests speech and releases microphone", async () => {
  const app = setup({ unauthorized: true });
  await app.elements.start.emit("click");
  assert.equal(app.calls.length, 1);
  assert.equal(app.blobs.length, 0);
  assert.equal(app.track.stopped, true);
  assert.match(app.elements.status.textContent, /Operator token/);
  assert.equal(app.elements.start.disabled, false);
});

test("speech fetch failure keeps the question visible and disables microphone", async () => {
  const app = setup({ speechFailure: true });
  await app.elements.start.emit("click");
  assert.equal(app.blobs.length, 0);
  assert.equal(app.track.stopped, true);
  assert.match(app.elements.status.textContent, /voice could not play/);
  assert.ok(app.elements.conversation.children.some(row => row.textContent.includes("Synthetic question")));
});
