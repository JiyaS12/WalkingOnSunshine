/* Read-only dashboard. All patient/model text is inserted with textContent. */
const ResultData = (() => {
  const statusLabels = { completed: "Completed", in_progress: "In progress", needs_review: "Needs review", scheduled: "Scheduled", failed: "Failed" };
  function label(value) {
    if (value && typeof value === "object") value = value.value ?? value.label;
    return typeof value === "string" && value.trim() ? value.trim() : null;
  }
  function answers(row, catalog = {}) {
    const byKey = new Map();
    for (const item of Array.isArray(row.survey_results) ? row.survey_results : []) {
      if (item && typeof item.question_key === "string") byKey.set(item.question_key, item);
    }
    const category = [...byKey.keys()].map(key => catalog[key]?.category).find(Boolean);
    const known = category ? Object.keys(catalog).filter(key => catalog[key].category === category) : [];
    const keys = [...new Set([...known, ...byKey.keys()])];
    return keys.sort((a, b) => (catalog[a]?.order ?? 999) - (catalog[b]?.order ?? 999)).map(key => {
      const item = byKey.get(key);
      const confirmed = !!item && ["confirmed", "corrected"].includes(item.confirmation_status) && label(item.confirmed_value) !== null;
      return { key, question: catalog[key]?.prompt || item?.question_text || key.replaceAll("_", " "),
        confirmed, status: confirmed ? item.confirmation_status : item ? "pending" : "unanswered",
        value: item ? label(confirmed ? item.confirmed_value : item.ai_proposed_value ?? item.confirmed_value) : null,
        raw: typeof item?.raw_patient_text === "string" ? item.raw_patient_text : "" };
    });
  }
  function normalize(row, catalog, index) {
    const entries = answers(row, catalog);
    const total = entries.length;
    const answered = entries.filter(item => item.confirmed).length;
    const validScore = row.total_score !== null && row.total_score !== undefined && row.total_score !== "" && Number.isFinite(Number(row.total_score));
    return { ...row, key: String(row.survey_instance_id || row.session_id || row.call_session_id || `row-${index}`),
      patient: String(row.patient_id || "Unknown patient"),
      status: Object.hasOwn(statusLabels, row.status) ? row.status : "unknown",
      entries, total, answered,
      // A partial survey or missing value must never look like a zero score.
      score: row.status === "completed" && total > 0 && answered === total && validScore ? Number(row.total_score) : null,
      timestamp: Date.parse(row.started_at || row.completed_at || "") || 0 };
  }
  function transcript(text) {
    const turns = [];
    for (const line of String(text || "").split("\n")) {
      const match = line.match(/^(assistant|patient):\s?(.*)$/);
      if (match) turns.push({ speaker: match[1], text: match[2] });
      else if (turns.length) turns[turns.length - 1].text += "\n" + line;
      else if (line.trim()) turns.push({ speaker: "note", text: line });
    }
    return turns;
  }
  function filter(rows, query, status) {
    return rows.filter(row => row.patient.toLowerCase().includes(query.trim().toLowerCase()) && (status === "all" || row.status === status));
  }
  return { statusLabels, label, answers, normalize, transcript, filter };
})();

if (typeof module !== "undefined") module.exports = ResultData;

function mountResults() {
  const $ = id => document.getElementById(id);
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  let rows = [];
  let selected = null;
  let lastDetail = "";
  let lastKey = null;
  let loading = false;
  let loaded = false;
  let controller = null;
  let requestVersion = 0;
  let token = "";
  try { token = sessionStorage.getItem("operatorToken") || ""; } catch { /* Storage may be disabled. */ }
  const badge = (status, text) => el("span", `badge ${status}`, text || ResultData.statusLabels[status] || "Unknown");
  const date = value => {
    const parsed = new Date(value);
    return value && !Number.isNaN(parsed.getTime()) ? parsed.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "Date not recorded";
  };
  const followUp = row => row.follow_up_label?.startsWith("live:") || !row.follow_up_label ? "Voice survey" : String(row.follow_up_label);

  function renderDetail(row) {
    const fingerprint = JSON.stringify(row);
    if (fingerprint === lastDetail) return;
    const expanded = new Set();
    if (row && lastKey === row.key) {
      $("detail").querySelectorAll("details[data-section]").forEach(node => { if (node.open) expanded.add(node.dataset.section); });
    }
    lastDetail = fingerprint;
    lastKey = row?.key;
    const root = $("detail");
    root.replaceChildren();
    if (!row) {
      const empty = el("div", "empty detail-empty");
      empty.append(el("span", "empty-icon", "↗"), el("h2", "Nothing to review yet"), el("p", "Start a voice survey, or adjust your filters to find a conversation."));
      root.append(empty);
      return;
    }
    const header = el("header", "detail-header");
    const top = el("div", "detail-top");
    const heading = el("div");
    heading.append(el("p", "detail-eyebrow", "PATIENT SURVEY"), el("h2", "detail-title", row.patient), el("p", "detail-subtitle", `${followUp(row)} · ${date(row.started_at || row.completed_at)}`));
    top.append(heading, badge(row.status));
    const overview = el("div", "overview");
    const progress = el("div");
    progress.append(el("span", "overview-label", "Confirmed answers"));
    const count = el("div", "overview-value", String(row.answered));
    count.append(el("small", "", row.total ? ` / ${row.total} questions` : " recorded"));
    progress.append(count);
    const bar = el("div", "progress");
    const fill = el("span");
    fill.style.width = `${row.total ? row.answered / row.total * 100 : 0}%`;
    bar.append(fill);
    progress.append(bar);
    const score = el("div");
    score.append(el("span", "overview-label", "Prototype score"));
    const value = el("div", "overview-value", row.score === null ? "—" : String(row.score));
    if (row.score !== null) value.append(el("small", "", " points"));
    score.append(value, el("p", "score-note", row.score === null ? "Available when all answers are confirmed." : "Sum of item weights; not an official clinical score."));
    overview.append(progress, score);
    header.append(top, overview);
    if (row.storage_source === "in_memory") header.append(el("p", "score-note", "This record is a temporary local copy, not a verified Supabase result."));
    root.append(header);
    const sectionHeading = el("div", "section-heading");
    sectionHeading.append(el("h2", "", "Recorded answers"), el("span", "", "Pending interpretations are not scored"));
    root.append(sectionHeading);
    const answers = el("div", "answers");
    if (!row.entries.length) answers.append(el("p", "empty", "No answers have been recorded for this survey yet."));
    row.entries.forEach((answer, index) => {
      const section = el("section", "answer");
      const top = el("div", "answer-top");
      const body = el("div", "answer-body");
      body.append(el("p", "question", answer.question));
      const state = el("div", "answer-state");
      if (answer.status === "unanswered") state.append(el("span", "answer-missing", "Not answered"));
      else {
        const text = answer.value ? answer.value[0].toUpperCase() + answer.value.slice(1) : "No category recorded";
        state.append(el("span", "answer-value", text), badge(answer.status, answer.confirmed ? answer.status === "corrected" ? "Corrected & confirmed" : "Confirmed" : "Pending · not scored"));
      }
      body.append(state);
      if (answer.raw) {
        const quote = el("details", "quote-details");
        quote.dataset.section = answer.key;
        quote.open = expanded.has(answer.key);
        quote.append(el("summary", "", "Patient’s words"), el("blockquote", "patient-quote", answer.raw));
        body.append(quote);
      }
      top.append(el("span", "number", String(index + 1).padStart(2, "0")), body);
      section.append(top);
      answers.append(section);
    });
    root.append(answers);
    const transcript = el("details", "transcript-section");
    transcript.dataset.section = "transcript";
    transcript.open = expanded.has("transcript");
    const turns = ResultData.transcript(row.transcript);
    transcript.append(el("summary", "", `Conversation transcript${turns.length ? ` · ${turns.length} turns` : ""}`));
    const chat = el("div", "transcript");
    if (!turns.length) chat.append(el("p", "empty", "No transcript stored. Seeded or not-yet-started surveys may have answers without a conversation."));
    turns.forEach(turn => {
      const bubble = el("div", `turn ${turn.speaker}`);
      bubble.append(el("p", "turn-label", turn.speaker === "patient" ? "PATIENT" : turn.speaker === "assistant" ? "SURVEY HELPER" : "TRANSCRIPT"), el("p", "turn-text", turn.text));
      chat.append(bubble);
    });
    transcript.append(chat);
    root.append(transcript, el("p", "record-reference", `Record reference: ${row.key}`));
  }

  function render() {
    const visible = ResultData.filter(rows, $("search").value, $("filter").value);
    if (!visible.some(row => row.key === selected)) selected = visible[0]?.key || null;
    const focusKey = document.activeElement?.dataset?.key;
    $("sessions").replaceChildren();
    $("count").textContent = `${visible.length} shown`;
    if (!visible.length) $("sessions").append(el("p", "empty", rows.length ? "No conversations match these filters." : "No surveys yet. Open the voice survey to begin."));
    visible.forEach(row => {
      const card = el("button", `session-card${row.key === selected ? " selected" : ""}`);
      card.type = "button";
      card.dataset.key = row.key;
      card.setAttribute("aria-pressed", String(row.key === selected));
      const top = el("div", "session-top");
      top.append(el("span", "patient-code", row.patient), badge(row.status));
      const bottom = el("div", "session-bottom");
      bottom.append(el("span", "", row.total ? `${row.answered}/${row.total} confirmed` : "No answers yet"), el("span", "", row.score === null ? "Score —" : `Score ${row.score}`));
      card.append(top, el("p", "session-meta", `${followUp(row)} · ${date(row.started_at || row.completed_at)}`), bottom);
      card.addEventListener("click", () => { selected = row.key; render(); });
      $("sessions").append(card);
      if (focusKey === row.key) card.focus({ preventScroll: true });
    });
    $("total").textContent = rows.length;
    $("completed").textContent = rows.filter(row => row.status === "completed").length;
    $("active").textContent = rows.filter(row => row.status === "in_progress").length;
    $("review").textContent = rows.filter(row => row.status === "needs_review").length;
    renderDetail(visible.find(row => row.key === selected));
  }

  function lockResults(message = "Enter your operator token to load results.") {
    requestVersion++;
    controller?.abort();
    loading = false;
    loaded = false;
    token = "";
    try { sessionStorage.removeItem("operatorToken"); } catch { /* No storage. */ }
    $("operator-token").value = "";
    $("access-form").hidden = false;
    $("lock").hidden = true;
    rows = [];
    selected = null;
    render();
    $("source").textContent = "Locked";
    $("source").className = "source local";
    $("updated").textContent = "No records loaded";
    $("notice").textContent = message;
    $("notice").hidden = false;
    $("refresh").disabled = true;
  }

  async function load() {
    if (!token) return;
    if (loading) return;
    loading = true;
    const version = ++requestVersion;
    $("refresh").disabled = true;
    const currentController = new AbortController();
    controller = currentController;
    const timeout = setTimeout(() => currentController.abort(), 25000);
    try {
      const response = await fetch("/api/results", { cache: "no-store", signal: currentController.signal,
        headers: { Authorization: `Bearer ${token}` } });
      if (version !== requestVersion) return;
      if ([401, 403, 503].includes(response.status)) {
        lockResults(response.status === 503 ? "The server is not configured for operator access. Set OPERATOR_TOKEN and restart it." : "Operator token missing or incorrect. Results have been cleared.");
        return;
      }
      if (!response.ok) throw new Error("Results request failed");
      const data = await response.json();
      if (version !== requestVersion) return;
      if (!Array.isArray(data.results)) throw new Error("Invalid results response");
      rows = data.results.filter(row => row && typeof row === "object").map((row, index) => ResultData.normalize(row, data.question_catalog || {}, index)).sort((a, b) => b.timestamp - a.timestamp);
      loaded = true;
      $("access-form").hidden = true;
      $("operator-token").value = "";
      $("lock").hidden = false;
      const database = data.source === "supabase";
      $("source").textContent = database ? "Read from Supabase" : data.source === "mixed" ? "Supabase + temporary local results" : "Temporary local storage";
      $("source").className = database ? "source" : "source local";
      $("notice").textContent = data.warning || (!database ? "These results are only held in memory and disappear when the app restarts." : "");
      $("notice").hidden = !$("notice").textContent;
      $("updated").textContent = `Updated ${new Date().toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" })}`;
      render();
    } catch {
      if (version !== requestVersion) return;
      $("source").textContent = "Refresh failed";
      $("source").className = "source error";
      $("notice").hidden = false;
      $("notice").textContent = loaded ? "Could not refresh results. Showing the last successful update; these results may be out of date." : "Could not load results. Check that the local app is running, then press Refresh.";
      if (!loaded) $("sessions").replaceChildren(el("p", "empty", "Results unavailable. Press Refresh to retry."));
    } finally {
      clearTimeout(timeout);
      if (version === requestVersion) {
        loading = false;
        $("refresh").disabled = false;
      }
    }
  }
  $("search").value = new URLSearchParams(window.location.search).get("patient_code") || "";
  $("search").addEventListener("input", render);
  $("filter").addEventListener("change", render);
  $("refresh").addEventListener("click", load);
  $("access-form").addEventListener("submit", event => {
    event.preventDefault();
    const entered = $("operator-token").value.trim();
    lockResults();
    if (!entered) return;
    token = entered;
    try { sessionStorage.setItem("operatorToken", token); } catch { /* Use in-memory token. */ }
    load();
  });
  $("lock").addEventListener("click", () => lockResults());
  const timer = setInterval(() => { if (document.visibilityState === "visible") load(); }, 10000);
  window.addEventListener("pagehide", () => { clearInterval(timer); controller?.abort(); });
  if (token) load();
  else lockResults();
}

if (typeof document !== "undefined") mountResults();
