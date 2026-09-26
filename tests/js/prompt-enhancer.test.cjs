const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/prompt-enhancer.js', 'utf8');

test('shows safe missing-key development error', async () => {
  const ui = setup(async () => ({ok: false, json: async () => ({code: 'configuration'})}));
  await ui.handlers.click();
  assert.equal(ui.feedback.textContent, 'OpenRouter API key is not configured.');
});

function setup(fetcher, value = 'sleep and grades') {
  const handlers = {};
  const button = {disabled: false, textContent: 'Enhance Prompt', dataset: {url: '/enhance-prompt/'}, addEventListener: (event, fn) => handlers.click = fn};
  const form = {getAttribute: () => null, querySelector: () => ({value: 'csrf-token'}), addEventListener: (event, fn) => handlers.submit = fn};
  const textarea = {value, dispatchEvent() {}, focus() {}};
  const feedback = {textContent: ''};
  const nodes = {'enhance-prompt': button, 'question-form': form, 'id_question': textarea, 'enhance-feedback': feedback};
  vm.runInNewContext(source, {document: {getElementById: id => nodes[id]}, fetch: fetcher, AbortController, Event, setTimeout, clearTimeout});
  return {handlers, button, textarea, feedback};
}

test('replaces textarea, sends CSRF, restores button and never submits', async () => {
  let options;
  const ui = setup(async (url, opts) => {assert.equal(url, '/enhance-prompt/'); options = opts; return {ok: true, json: async () => ({enhanced_text: 'Academic question?'})};});
  const pending = ui.handlers.click();
  assert.equal(ui.button.disabled, true);
  await pending;
  assert.equal(ui.textarea.value, 'Academic question?');
  assert.equal(options.method, 'POST');
  assert.equal(options.headers['X-CSRFToken'], 'csrf-token');
  assert.equal(options.credentials, 'same-origin');
  assert.equal(JSON.parse(options.body).text, 'sleep and grades');
  assert.equal(ui.button.disabled, false);
  assert.match(ui.feedback.textContent, /press Research/);
});

test('empty and oversized inputs never request', async () => {
  for (const text of ['', '  ', 'x'.repeat(2001)]) {
    const ui = setup(() => assert.fail('unexpected request'), text);
    await ui.handlers.click();
    assert.ok(ui.feedback.textContent);
  }
});

test('failure preserves input and enables retry', async () => {
  const ui = setup(async () => ({ok: false, json: async () => ({error: 'raw secret'})}));
  await ui.handlers.click();
  assert.equal(ui.textarea.value, 'sleep and grades');
  assert.equal(ui.button.disabled, false);
  assert.doesNotMatch(ui.feedback.textContent, /raw secret/);
});

test('does not overwrite edits made while waiting', async () => {
  let resolve;
  const ui = setup(() => new Promise(r => resolve = r));
  const pending = ui.handlers.click();
  ui.textarea.value = 'new idea';
  resolve({ok: true, json: async () => ({enhanced_text: 'old enhanced idea'})});
  await pending;
  assert.equal(ui.textarea.value, 'new idea');
});

test('normal research submission aborts enhancement', async () => {
  let resolve;
  let signal;
  const ui = setup((url, options) => {signal = options.signal; return new Promise(r => resolve = r);});
  const pending = ui.handlers.click();
  ui.handlers.submit();
  assert.equal(signal.aborted, true);
  resolve({ok: true, json: async () => ({enhanced_text: 'late result'})});
  await pending;
  assert.equal(ui.textarea.value, 'sleep and grades');
});
