const dialer = document.querySelector("#dialer");
const button = document.querySelector("#call");
const configLine = document.querySelector("#config");
const errorLine = document.querySelector("#error");
const live = document.querySelector("#live");
const badge = document.querySelector("#badge");
const statusLine = document.querySelector("#status");
const transcript = document.querySelector("#transcript");
const answers = document.querySelector("#answers");
const answerCount = document.querySelector("#answer-count");
const metaTo = document.querySelector("#meta-to");
const metaPatient = document.querySelector("#meta-patient");
const metaSid = document.querySelector("#meta-sid");

const BADGES = {
  dialing: ["Dialing", "pending"],
  in_progress: ["On the call", "live"],
  completed: ["Finished", "done"],
};

let pollTimer = null;
let renderedTranscript = "";

async function loadConfig() {
  try {
    const response = await fetch("/api/config");
    const data = await response.json();
    if (data.ready) {
      configLine.textContent = data.llm_configured
        ? `Ready — Twilio, Deepgram and conversational answers configured, webhooks at ${data.public_base_url}`
        : `Ready, but answers are matched word for word — set OPENAI_API_KEY and SURVEY_EXTRACTOR=openai so phrases like "a moderate amount" are understood.`;
      configLine.classList.add(data.llm_configured ? "ok" : "warn");
      return;
    }
    const missing = [];
    if (!data.twilio_configured) missing.push("Twilio credentials");
    if (!data.deepgram_configured) missing.push("Deepgram key");
    if (!data.public_base_url) missing.push("PUBLIC_BASE_URL");
    configLine.textContent = `Not ready — set ${missing.join(", ")} in .env, then restart the server.`;
    configLine.classList.add("warn");
    button.disabled = true;
  } catch {
    configLine.textContent = "Could not reach the server.";
    configLine.classList.add("warn");
    button.disabled = true;
  }
}

function showError(message) {
  errorLine.textContent = message;
  errorLine.hidden = !message;
}

function setBadge(status) {
  const [label, tone] = BADGES[status] || [status, "pending"];
  badge.textContent = label;
  badge.dataset.tone = tone;
}

function renderTranscript(lines) {
  if (!lines.length) return;
  const signature = lines.join("\n");
  if (signature === renderedTranscript) return;
  renderedTranscript = signature;
  // Rebuilding the list resets the scroll position, so only follow the
  // conversation when the operator is already reading the latest turn.
  const following =
    transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 40;
  transcript.replaceChildren();
  for (const line of lines) {
    const [speaker, ...rest] = line.split(": ");
    const text = rest.join(": ") || line;
    const item = document.createElement("li");
    item.className = speaker === "patient" ? "turn patient" : "turn assistant";
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = speaker === "patient" ? "Patient" : "Assistant";
    item.append(who, document.createTextNode(text));
    transcript.appendChild(item);
  }
  if (following) transcript.scrollTop = transcript.scrollHeight;
}

function renderAnswers(rows) {
  answerCount.textContent = String(rows.length);
  if (!rows.length) return;
  answers.replaceChildren();
  for (const row of rows) {
    const tr = document.createElement("tr");
    const question = document.createElement("td");
    question.textContent = row.question_id;
    const value = document.createElement("td");
    value.textContent = row.value;
    tr.append(question, value);
    answers.appendChild(tr);
  }
}

function describeOutcome(record) {
  if (record.final_status === "complete") return "Survey completed; answers are ready for handoff.";
  if (record.final_status === "escalated") return "Escalated — a clinician should follow up.";
  if (record.final_status === "no-answer") return "The patient did not answer.";
  if (record.final_status === "busy") return "The line was busy.";
  if (record.final_status === "failed") return "Twilio could not connect the call.";
  return `Call ended (${record.final_status || "unknown"}).`;
}

function render(record) {
  setBadge(record.status);
  metaTo.textContent = record.to_number || "—";
  metaPatient.textContent = `${record.patient_code} (${record.condition_category})`;
  metaSid.textContent = record.call_sid || "—";
  renderTranscript(record.transcript);
  renderAnswers(record.answers);
  if (record.status === "completed") {
    statusLine.textContent = describeOutcome(record);
    return true;
  }
  statusLine.textContent =
    record.status === "dialing"
      ? `Ringing${record.carrier_status ? ` (${record.carrier_status})` : ""} — the survey starts when the patient answers.`
      : "Survey in progress.";
  return false;
}

function stopPolling() {
  clearInterval(pollTimer);
  button.disabled = false;
  button.textContent = "Call patient";
}

function pollCall(sessionId) {
  clearInterval(pollTimer);
  // A restarted server forgets in-memory calls; give up rather than poll forever.
  let misses = 0;
  pollTimer = setInterval(async () => {
    const response = await fetch(`/api/calls/${sessionId}`);
    if (!response.ok) {
      if (++misses < 8) return;
      stopPolling();
      showError("The server no longer has this call — reload the page and dial again.");
      return;
    }
    misses = 0;
    if (render(await response.json())) stopPolling();
  }, 1500);
}

dialer.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  button.disabled = true;
  button.textContent = "Dialing…";
  transcript.replaceChildren();
  renderedTranscript = "";
  answers.replaceChildren();
  live.hidden = false;
  setBadge("dialing");
  statusLine.textContent = "Placing the call…";

  let data;
  try {
    const response = await fetch("/api/calls", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        to_number: document.querySelector("#to").value.trim(),
        patient_code: document.querySelector("#patient").value,
      }),
    });
    data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not place the call.");
  } catch (err) {
    showError(err.message);
    live.hidden = true;
    button.disabled = false;
    button.textContent = "Call patient";
    return;
  }

  metaTo.textContent = data.to_number;
  metaSid.textContent = data.call_sid;
  pollCall(data.session_id);
});

loadConfig();
