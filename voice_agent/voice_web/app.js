let sessionId = null;
let capture = null;
let busy = false;
let finished = false;
let micPaused = true;
let awaitingUser = false;
let microphone = null;
let audioContext = null;
let analyser = null;

const conversation = document.querySelector("#conversation");
const recordButton = document.querySelector("#record");
const listenButton = document.querySelector("#listen-toggle");
const status = document.querySelector("#status");
const startButton = document.querySelector("#start");
const player = document.querySelector("#voice");
let playerUrl = null;

function updateControls() {
  startButton.disabled = busy || (!!sessionId && !finished);
  recordButton.disabled = busy || !capture || finished;
  listenButton.disabled = !sessionId || finished || (busy && micPaused);
  listenButton.textContent = micPaused ? "Resume listening" : "Pause microphone";
}

const tokenField = document.querySelector("#token");
tokenField.value = sessionStorage.getItem("operatorToken") || "";

function operatorHeaders() {
  const token = tokenField.value.trim();
  sessionStorage.setItem("operatorToken", token);
  return { Authorization: `Bearer ${token}` };
}

async function readResponse(response) {
  const data = await response.json();
  if (!response.ok) {
    if (data.transcript) addLine("user", data.transcript);
    throw new Error(data.detail || "The request failed. Please try again.");
  }
  return data;
}

function addLine(speaker, text) {
  const line = document.createElement("p");
  line.className = speaker;
  line.textContent = `${speaker === "assistant" ? "Survey" : "You"}: ${text}`;
  conversation.appendChild(line);
  line.scrollIntoView({ behavior: "smooth", block: "end" });
}

function enableMicrophone(enabled) {
  microphone?.getTracks().forEach(track => { track.enabled = enabled; });
}

function releaseMicrophone() {
  finishCapture(false);
  microphone?.getTracks().forEach(track => track.stop());
  microphone = null;
  audioContext?.close().catch(() => {});
  audioContext = null;
  analyser = null;
}

async function openMicrophone() {
  if (microphone) return;
  const Context = window.AudioContext || window.webkitAudioContext;
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder || !Context) {
    throw new Error("Hands-free recording is not supported here. Open this page in Chrome or Safari.");
  }
  // Resume within the initial click so later turns need no extra gesture.
  audioContext = new Context();
  await audioContext.resume();
  microphone = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  analyser = audioContext.createAnalyser();
  analyser.fftSize = 2048;
  audioContext.createMediaStreamSource(microphone).connect(analyser);
  // No speaker connection: never monitor the patient's own microphone.
  enableMicrophone(false);
}

function fail(error) {
  micPaused = true;
  releaseMicrophone();
  status.textContent = error.name === "NotAllowedError"
    ? "Microphone permission was denied. Allow access, then try again."
    : error.name === "NotFoundError"
      ? "No microphone was found. Connect a microphone, then try again."
      : `${error.message} ${sessionId && !finished ? "Press Resume listening to try again." : ""}`;
  updateControls();
}

function stopPlayback() {
  player.pause();
  player.removeAttribute("src");
  player.load();
  player.hidden = true;
  window.speechSynthesis?.cancel();
}

function voiceFailed() {
  awaitingUser = true;
  micPaused = true;
  releaseMicrophone();
  status.textContent = "The voice could not play. Press Play to retry, or read the question and press Resume listening.";
  updateControls();
}

async function speak(data) {
  awaitingUser = false;
  enableMicrophone(false);
  stopPlayback();
  status.textContent = "Preparing the helper’s voice...";
  try {
    // The audio element cannot send the operator token, so fetch the speech
    // ourselves and hand the player a local object URL.
    const response = await fetch(
      `/api/sessions/${sessionId}/speech?prompt_id=${encodeURIComponent(data.prompt_id)}`,
      { headers: operatorHeaders() },
    );
    if (!response.ok) throw new Error("The voice could not be fetched.");
    if (playerUrl) URL.revokeObjectURL(playerUrl);
    playerUrl = URL.createObjectURL(await response.blob());
    player.src = playerUrl;
    player.preload = "auto";
    player.hidden = false;
    player.load();
    await player.play();
    status.textContent = "The helper is speaking. You can answer when the voice finishes.";
  } catch {
    voiceFailed();
  }
}

player.addEventListener("play", () => {
  // Replays discard partial replies; never transcribe the helper's voice.
  finishCapture(false);
  awaitingUser = false;
  enableMicrophone(false);
  updateControls();
});
player.addEventListener("ended", () => {
  awaitingUser = true;
  if (finished) status.textContent = "Survey finished. Microphone off.";
  else if (micPaused) status.textContent = "Microphone paused. Press Resume listening when ready.";
  beginListening();
});
player.addEventListener("pause", () => {
  // Pausing midway through a prompt is not the same as the prompt ending.
  if (sessionId && !finished && !busy && !awaitingUser && player.paused && !player.ended) {
    status.textContent = "Voice paused. Press Play to finish the question, or Resume listening to answer.";
    micPaused = true;
    releaseMicrophone();
    updateControls();
  }
});
player.addEventListener("error", voiceFailed);

async function sendRecording(blob, recordedSessionId) {
  busy = true;
  awaitingUser = false;
  updateControls();
  status.textContent = "Understanding your answer...";
  try {
    if (!blob.size) throw new Error("The recording was empty.");
    const form = new FormData();
    form.append("audio", blob, "answer.webm");
    const response = await fetch(`/api/sessions/${recordedSessionId}/audio`, {
      method: "POST",
      body: form,
      headers: operatorHeaders(),
    });
    const data = await readResponse(response);
    addLine("user", data.transcript);
    addLine("assistant", data.prompt);
    finished = ["complete", "escalated", "stopped"].includes(data.state);
    if (finished) releaseMicrophone();
    await speak(data);
  } catch (error) {
    awaitingUser = true;
    fail(error);
  } finally {
    busy = false;
    updateControls();
    beginListening();
  }
}

function finishCapture(send) {
  const active = capture;
  if (!active) return;
  capture = null;
  clearInterval(active.timer);
  active.send = send;
  enableMicrophone(false);
  if (send) {
    busy = true;
    awaitingUser = false;
  }
  if (active.recorder.state !== "inactive") active.recorder.stop();
  updateControls();
}

function beginListening() {
  if (busy || finished || micPaused || !awaitingUser || !sessionId || capture || !microphone || !player.paused) return;
  try {
    enableMicrophone(true);
    const mimeType = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"].find(type => MediaRecorder.isTypeSupported(type));
    const recorder = new MediaRecorder(microphone, mimeType ? { mimeType } : undefined);
    const active = { recorder, chunks: [], send: false, session: sessionId, timer: null };
    capture = active;
    const detector = new TurnDetector();
    const samples = new Float32Array(analyser.fftSize);
    const started = performance.now();
    recorder.addEventListener("dataavailable", event => {
      if (event.data.size) active.chunks.push(event.data);
    });
    recorder.addEventListener("stop", async () => {
      if (active.send && active.session === sessionId) {
        await sendRecording(new Blob(active.chunks, { type: recorder.mimeType || "audio/webm" }), active.session);
      }
    });
    recorder.addEventListener("error", () => {
      if (capture === active) fail(new Error("The microphone recording failed."));
    });
    recorder.start();
    active.timer = setInterval(() => {
      if (capture !== active) return;
      analyser.getFloatTimeDomainData(samples);
      const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
      const now = performance.now();
      const turn = detector.update(rms, now);
      status.textContent = turn.hasSpeech
        ? "Listening. I’ll send your answer after a brief pause."
        : "Listening. Take your time, then speak your answer.";
      if (turn.complete || (turn.hasSpeech && now - started >= 120000)) {
        finishCapture(true);
      } else if (!turn.hasSpeech && now - started >= 30000) {
        // Bound memory during quiet waits; never upload silence alone.
        finishCapture(false);
        beginListening();
      }
    }, 50);
    status.textContent = "Listening. Take your time, then speak your answer.";
    updateControls();
  } catch (error) {
    fail(error);
  }
}

startButton.addEventListener("click", async () => {
  if (busy || (sessionId && !finished)) return;
  busy = true;
  finished = false;
  awaitingUser = false;
  sessionId = null;
  micPaused = false;
  stopPlayback();
  releaseMicrophone();
  updateControls();
  conversation.replaceChildren();
  status.textContent = "Opening your microphone...";
  try {
    await openMicrophone();
    const patientCode = document.querySelector("#patient").value.trim();
    const response = await fetch(`/api/sessions?patient_code=${encodeURIComponent(patientCode)}`, {
      method: "POST",
      headers: operatorHeaders(),
    });
    const data = await readResponse(response);
    sessionId = data.session_id;
    addLine("assistant", data.prompt);
    await speak(data);
  } catch (error) {
    fail(error);
  } finally {
    busy = false;
    updateControls();
    beginListening();
  }
});

listenButton.addEventListener("click", async () => {
  if (!sessionId || finished) return;
  if (!micPaused) {
    micPaused = true;
    releaseMicrophone();
    status.textContent = "Microphone paused. Unsent audio discarded. Press Resume listening when ready.";
    updateControls();
    return;
  }
  if (busy) return;
  busy = true;
  updateControls();
  try {
    await openMicrophone();
    awaitingUser = true;
    stopPlayback();
    micPaused = false;
  } catch (error) {
    fail(error);
  } finally {
    busy = false;
    updateControls();
    beginListening();
  }
});

recordButton.addEventListener("click", () => {
  if (!busy && capture && !finished) finishCapture(true);
});
window.addEventListener("pagehide", () => {
  micPaused = true;
  releaseMicrophone();
});
