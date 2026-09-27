const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function setup(mobile = true) {
  const events = {};
  const add = (type, fn) => (events[type] ||= []).push(fn);
  function node() {
    const classes = new Set();
    return {style: {}, attrs: {}, hidden: true, open: false,
      classList: {contains: x => classes.has(x), add: x => classes.add(x), remove: x => classes.delete(x)},
      setAttribute(k,v) {this.attrs[k] = v;}, focus() {document.activeElement = this;},
      contains(x) {return x === this;}, closest() {return null;}, addEventListener() {},
      getBoundingClientRect: () => ({left: 0, right: 280, width: 280, top: 680, bottom: 710})};
  }
  const sidebar = node(), toggle = node(), backdrop = node(), search = node();
  const menus = [node(), node()];
  for (const menu of menus) {
    menu.summary = node(); menu.popup = {...node(), scrollHeight: 140};
    menu.summary.parentElement = menu;
    menu.summary.closest = selector => selector === '.chat-menu > summary' ? menu.summary : null;
    menu.querySelector = selector => selector === 'summary' ? menu.summary : menu.popup;
    menu.contains = target => target === menu.summary;
  }
  sidebar.querySelectorAll = selector => selector === '.chat-menu' ? menus : [];
  sidebar.querySelector = selector => selector === '#chat-search' ? search : null;
  sidebar.contains = target => target === search || target === sidebar || target.inside;
  const document = {activeElement: null, getElementById: id => ({sidebar, 'sidebar-toggle': toggle, 'sidebar-backdrop': backdrop}[id]), addEventListener: add};
  const window = {matchMedia: () => ({matches: mobile}), innerWidth: 375, innerHeight: 740, addEventListener: add};
  vm.runInNewContext(fs.readFileSync('static/js/sidebar-interactions.js','utf8'), {document,window});
  return {sidebar,toggle,backdrop,menus,document, fire(type, target, extra = {}) {
    for (const fn of events[type] || []) fn({target, preventDefault() {}, ...extra});
  }, outside: node()};
}

test('only one popup stays open, outside click and Escape close it', () => {
  const ui = setup(false);
  ui.fire('click', ui.menus[0].summary);
  assert.equal(ui.menus[0].open, true);
  assert.equal(ui.menus[0].summary.attrs['aria-expanded'], 'true');
  assert.ok(parseFloat(ui.menus[0].popup.style.top) < 680);
  ui.fire('click', ui.menus[1].summary);
  assert.equal(ui.menus[0].open, false);
  assert.equal(ui.menus[1].open, true);
  ui.fire('click', ui.outside);
  assert.equal(ui.menus[1].open, false);
  ui.fire('click', ui.menus[0].summary);
  ui.fire('keydown', ui.menus[0].summary, {key: 'Escape'});
  assert.equal(ui.menus[0].open, false);
  assert.equal(ui.document.activeElement, ui.menus[0].summary);
});

test('drawer opens, backdrop closes, navigation closes and desktop stays unchanged', () => {
  const ui = setup();
  ui.fire('click', ui.toggle);
  assert.equal(ui.backdrop.hidden, false);
  assert.equal(ui.toggle.attrs['aria-expanded'], 'true');
  ui.fire('click', ui.backdrop);
  assert.equal(ui.backdrop.hidden, true);
  assert.equal(ui.toggle.attrs['aria-expanded'], 'false');
  ui.fire('click', ui.toggle);
  const link = {inside: true, closest: selector => selector === '.history-item, .new-chat-form button' ? link : null};
  ui.fire('click', link);
  assert.equal(ui.backdrop.hidden, true);
  const desktop = setup(false);
  desktop.fire('click', desktop.toggle);
  assert.equal(desktop.sidebar.classList.contains('is-open'), false);
});

test('menu actions close popup without stealing modal focus', () => {
  const ui = setup();
  ui.fire('click', ui.menus[0].summary);
  const button = {inside: true, closest: selector => selector === '.chat-menu button' ? button : null};
  ui.fire('click', button);
  assert.equal(ui.menus[0].open, false);
  const dialogTarget = {closest: selector => selector === 'dialog[open]' ? dialogTarget : null};
  ui.fire('click', ui.toggle);
  ui.fire('click', dialogTarget);
  assert.equal(ui.backdrop.hidden, false);
});
