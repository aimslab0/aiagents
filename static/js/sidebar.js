"use strict";
(() => {
  const history = document.querySelector('.history');
  if (!history) return;
  const rows = Array.from(history.querySelectorAll('.history-row'));
  const key = 'research-pinned-chats';
  let pins = new Set();
  try {
    const saved = JSON.parse(localStorage.getItem(key) || '[]');
    if (Array.isArray(saved)) pins = new Set(saved.filter(id => typeof id === 'string'));
  } catch (_) {}
  const groups = new Map();
  for (const name of ['PINNED', 'TODAY', 'PREVIOUS 7 DAYS', 'OLDER']) {
    const section = document.createElement('section');
    section.className = 'history-group';
    const heading = document.createElement('h2');
    heading.textContent = name;
    section.append(heading);
    history.append(section);
    groups.set(name, section);
  }
  const search = document.getElementById('chat-search');
  const update = () => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const week = new Date(today);
    week.setDate(week.getDate() - 7);
    for (const row of rows) {
      const pinned = pins.has(row.dataset.chatId);
      const date = new Date(row.dataset.updated);
      const group = pinned ? 'PINNED' : date >= today ? 'TODAY' : date >= week ? 'PREVIOUS 7 DAYS' : 'OLDER';
      groups.get(group).append(row);
      const button = row.querySelector('[data-pin-chat]');
      button.textContent = pinned ? 'Unpin' : 'Pin';
      button.setAttribute('aria-pressed', String(pinned));
      row.hidden = !row.dataset.chatTitle.toLocaleLowerCase().includes((search?.value || '').trim().toLocaleLowerCase());
    }
    for (const section of groups.values()) {
      section.hidden = !Array.from(section.querySelectorAll('.history-row')).some(row => !row.hidden);
    }
  };
  for (const row of rows) row.querySelector('[data-pin-chat]').addEventListener('click', () => {
    const id = row.dataset.chatId;
    if (pins.has(id)) pins.delete(id); else pins.add(id);
    try { localStorage.setItem(key, JSON.stringify([...pins])); } catch (_) {}
    row.querySelector('.chat-menu').open = false;
    update();
    row.querySelector('.chat-menu summary').focus();
  });
  search?.addEventListener('input', update);
  window.addEventListener('pageshow', update);
  update();
})();
