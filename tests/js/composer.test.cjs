const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(maximum = 260, desktop = true) {
  const listeners = {};
  let submits = 0;
  const form = {getAttribute: () => null, requestSubmit: () => submits++};
  const textarea = {id: 'id_question', value: 'Research question', style: {}, scrollHeight: 140, form};
  const add = (event, fn) => (listeners[event] ||= []).push(fn);
  const document = {getElementById: id => id === 'id_question' ? textarea : null,
    querySelectorAll: () => [], addEventListener: add, documentElement: {classList: {add() {}}}};
  const window = {matchMedia: () => ({matches: desktop}), addEventListener: add, location: {hash: ''}, getComputedStyle: () => ({minHeight: '80px', maxHeight: `${maximum}px`})};
  vm.runInNewContext(fs.readFileSync('static/js/dashboard.js', 'utf8'), {document, window});
  return {textarea, form, submits: () => submits, fire: (event, data = {}) => listeners[event].forEach(fn => fn({target: textarea, ...data}))};
}

test('initial text grows, long text caps, clearing shrinks', () => {
  const ui = setup();
  assert.equal(ui.textarea.style.height, '140px');
  ui.textarea.scrollHeight = 500;
  ui.fire('input');
  assert.equal(ui.textarea.style.height, '260px');
  assert.equal(ui.textarea.style.overflowY, 'auto');
  ui.textarea.scrollHeight = 30;
  ui.fire('input');
  assert.equal(ui.textarea.style.height, '80px');
  assert.equal(ui.textarea.style.overflowY, 'hidden');
});

test('mobile cap applies to restored and enhanced text', () => {
  const ui = setup(200);
  ui.textarea.scrollHeight = 300;
  ui.fire('pageshow');
  assert.equal(ui.textarea.style.height, '200px');
  ui.textarea.scrollHeight = 180;
  ui.fire('input');
  assert.equal(ui.textarea.style.height, '180px');
});

test('desktop Enter submits; Shift, composition, empty input and busy state do not', () => {
  const ui = setup();
  const event = {key: 'Enter', preventDefault() {}};
  ui.fire('keydown', event);
  assert.equal(ui.submits(), 1);
  ui.fire('keydown', {...event, shiftKey: true});
  ui.fire('keydown', {...event, isComposing: true});
  ui.textarea.value = '  ';
  ui.fire('keydown', event);
  ui.textarea.value = 'Question';
  ui.form.getAttribute = () => 'true';
  ui.fire('keydown', event);
  assert.equal(ui.submits(), 1);
});

test('mobile Enter keeps newline and search Enter never submits', () => {
  const ui = setup(200, false);
  ui.fire('keydown', {key: 'Enter', preventDefault() {assert.fail('mobile newline prevented');}});
  assert.equal(ui.submits(), 0);
  let prevented = false;
  ui.fire('keydown', {target: {id: 'chat-search'}, key: 'Enter', preventDefault() {prevented = true;}});
  assert.equal(prevented, true);
  assert.equal(ui.submits(), 0);
});

test('Start Research focuses the existing input without submitting', () => {
  const ui = setup();
  let focused = false;
  ui.textarea.focus = () => { focused = true; };
  ui.textarea.scrollIntoView = () => {};
  const link = {hash: '#id_question'};
  ui.fire('click', {target: {closest: () => link}, preventDefault() {}});
  assert.equal(focused, true);
  assert.equal(ui.submits(), 0);
});
