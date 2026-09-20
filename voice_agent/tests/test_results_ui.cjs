const { test } = require('node:test');
const assert = require('node:assert/strict');
const data = require('../voice_web/results.js');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const catalog = {
  q1: { prompt: 'Exact first question?', category: 'hip', order: 1 },
  q2: { prompt: 'Exact second question?', category: 'hip', order: 2 },
  other: { prompt: 'Different survey?', category: 'other', order: 1 },
};
const answer = (key, value = 'mild', status = 'confirmed') => ({ question_key: key,
  confirmed_value: value, ai_proposed_value: value, confirmation_status: status });
const row = (answers, score = 2, status = 'completed') => data.normalize({
  patient_id: 'RGN-0417', survey_instance_id: 's1', status, total_score: score,
  survey_results: answers,
}, catalog, 0);

test('uses canonical question order and wording, only for the matching survey', () => {
  const result = row([answer('q2'), { ...answer('q1'), question_text: 'Changed wording' }]);
  assert.deepEqual(result.entries.map(x => x.question), ['Exact first question?', 'Exact second question?']);
  assert.equal(result.answered, 2);
  assert.equal(result.score, 2);
});
test('pending proposals are never counted as confirmed or scored', () => {
  const result = row([answer('q1', 'extreme', 'pending')], 4);
  assert.equal(result.answered, 0);
  assert.equal(result.score, null);
  assert.equal(result.entries[0].value, 'extreme');
  assert.equal(result.entries[0].status, 'pending');
  assert.equal(result.entries[1].status, 'unanswered');
});
test('a later confirmed correction replaces a pending duplicate', () => {
  const result = row([answer('q1', 'extreme', 'pending'), answer('q1', { value: 'severe' }, 'corrected'), answer('q2')], 4);
  assert.equal(result.entries.length, 2);
  assert.equal(result.entries[0].value, 'severe');
  assert.equal(result.entries[0].status, 'corrected');
  assert.equal(result.score, 4);
});
test('zero is a real score; missing or partial scores are not zero', () => {
  const complete = [answer('q1', 'none'), answer('q2', 'none')];
  assert.equal(row(complete, 0).score, 0);
  for (const score of [null, undefined, '', 'invalid']) {
    assert.equal(data.normalize({ status: 'completed', survey_results: complete, total_score: score }, catalog, 0).score, null);
  }
  assert.equal(row([answer('q1')], 1).score, null);
  assert.equal(row(complete, 0, 'in_progress').score, null);
});
test('empty and unconfirmed values remain unscored', () => {
  assert.equal(row([], null, 'scheduled').score, null);
  assert.equal(row([answer('q1', null)]).answered, 0);
  assert.equal(data.label({ label: ' mild ' }), 'mild');
});
test('transcript parsing retains multiline speech and treats markup as text', () => {
  assert.deepEqual(data.transcript('assistant: Hello\npatient: <script>bad()</script>\nand more'), [
    { speaker: 'assistant', text: 'Hello' },
    { speaker: 'patient', text: '<script>bad()</script>\nand more' },
  ]);
  assert.deepEqual(data.transcript(null), []);
});
test('search and status filtering combine without case sensitivity', () => {
  const rows = [row([]), { ...row([]), patient: 'RGN-0500', status: 'scheduled' }];
  assert.equal(data.filter(rows, ' rgn-04 ', 'all').length, 1);
  assert.equal(data.filter(rows, '', 'scheduled').length, 1);
  assert.equal(data.filter(rows, '0417', 'scheduled').length, 0);
});

function dashboard(initialToken = '') {
  class Element {
    constructor(tag = 'div') { this.tag = tag; this.children = []; this.dataset = {}; this.style = {}; this.listeners = {}; this.value = ''; this.hidden = false; this.text = ''; }
    set textContent(value) { this.text = String(value); this.children = []; }
    get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.text = ''; this.children = nodes; }
    setAttribute() {}
    focus() {}
    addEventListener(event, fn) { this.listeners[event] = fn; }
    querySelectorAll() { return this.children.flatMap(child => [...(child.tag === 'details' ? [child] : []), ...child.querySelectorAll()]); }
  }
  const ids = ['detail', 'sessions', 'count', 'search', 'filter', 'total', 'completed', 'active', 'review', 'refresh', 'source', 'updated', 'notice', 'operator-token', 'access-form', 'lock'];
  const nodes = Object.fromEntries(ids.map(id => [id, new Element()]));
  nodes.filter.value = 'all';
  const storage = new Map(initialToken ? [['operatorToken', initialToken]] : []);
  const calls = [];
  const responses = [];
  const context = {
    document: { getElementById: id => nodes[id], createElement: tag => new Element(tag), visibilityState: 'visible' },
    window: { location: { search: '' }, addEventListener() {} },
    sessionStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    fetch: async (url, options) => { calls.push({ url, options }); return responses.shift() || {ok:true, status:200, json:async () => ({source:'supabase', results:[]})}; },
    URLSearchParams, AbortController, setTimeout, clearTimeout, setInterval: () => 1, clearInterval() {},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../voice_web/results.js'), 'utf8'), context);
  return { nodes, calls, responses, storage };
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const unlock = ui => { ui.nodes['operator-token'].value = 'test-operator-0123456789'; ui.nodes['access-form'].listeners.submit({preventDefault(){}}); };

test('locked dashboard makes no request; unlock uses bearer header, never the URL', async () => {
  const ui = dashboard();
  assert.equal(ui.calls.length, 0);
  assert.equal(ui.nodes.source.textContent, 'Locked');
  unlock(ui);
  await flush();
  assert.equal(ui.calls[0].url, '/api/results');
  assert.equal(ui.calls[0].options.headers.Authorization, 'Bearer test-operator-0123456789');
  assert.equal(ui.calls[0].options.cache, 'no-store');
  assert.equal(ui.nodes.source.textContent, 'Read from Supabase');
  assert.equal(ui.nodes['operator-token'].value, '');
});
test('expired authentication clears previously rendered patient data and stored token', async () => {
  const ui = dashboard();
  ui.responses.push({ok:true, status:200, json:async () => ({source:'supabase', results:[{ patient_id:'SYNTHETIC-PRIVATE', status:'completed', transcript:'patient: test only', survey_results:[] }]})});
  unlock(ui);
  await flush();
  assert.match(ui.nodes.detail.textContent, /SYNTHETIC-PRIVATE/);
  ui.responses.push({ok:false, status:401});
  await ui.nodes.refresh.listeners.click();
  assert.doesNotMatch(ui.nodes.detail.textContent, /SYNTHETIC-PRIVATE/);
  assert.equal(ui.storage.size, 0);
  assert.equal(ui.nodes.source.textContent, 'Locked');
});
test('locking during a request prevents late response from redisplaying records', async () => {
  const ui = dashboard();
  let resolveJson;
  ui.responses.push({ok:true, status:200, json:() => new Promise(resolve => { resolveJson = resolve; })});
  unlock(ui);
  await flush();
  ui.nodes.lock.listeners.click();
  resolveJson({source:'supabase', results:[{patient_id:'LATE-PRIVATE'}]});
  await flush();
  assert.equal(ui.nodes.source.textContent, 'Locked');
  assert.doesNotMatch(ui.nodes.detail.textContent, /LATE-PRIVATE/);
});
