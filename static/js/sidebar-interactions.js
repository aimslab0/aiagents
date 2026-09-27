"use strict";
(() => {
  const sidebar = document.getElementById('sidebar');
  const toggle = document.getElementById('sidebar-toggle');
  const backdrop = document.getElementById('sidebar-backdrop');
  if (!sidebar || !toggle || !backdrop) return;
  const mobile = window.matchMedia('(max-width: 760px)');
  let activeMenu = null;

  function closeChatMenus(restoreFocus = false) {
    const previous = activeMenu;
    sidebar.querySelectorAll('.chat-menu').forEach(menu => {
      menu.open = false;
      menu.querySelector('summary').setAttribute('aria-expanded', 'false');
    });
    activeMenu = null;
    if (restoreFocus && previous) previous.querySelector('summary').focus();
  }
  function closeMobileSidebar(restoreFocus = false) {
    const wasOpen = sidebar.classList.contains('is-open');
    sidebar.classList.remove('is-open');
    backdrop.hidden = true;
    toggle.setAttribute('aria-expanded', String(!mobile.matches));
    if (restoreFocus && wasOpen) toggle.focus();
  }
  function closeAllPopovers(restoreFocus = false) {
    closeChatMenus();
    sidebar.querySelectorAll('.sidebar-settings[open]').forEach(node => { node.open = false; });
    closeMobileSidebar(restoreFocus);
  }
  function positionMenu(menu) {
    const popup = menu.querySelector('[role="menu"]');
    const anchor = menu.querySelector('summary').getBoundingClientRect();
    const bounds = sidebar.getBoundingClientRect();
    const width = Math.min(180, bounds.width - 16, window.innerWidth - 16);
    popup.style.width = `${width}px`;
    const height = Math.min(popup.scrollHeight, window.innerHeight - 16);
    const left = Math.max(bounds.left + 8, Math.min(anchor.right - width, bounds.right - width - 8, window.innerWidth - width - 8));
    const top = anchor.bottom + height + 4 <= window.innerHeight - 8 ? anchor.bottom + 4 : anchor.top - height - 4;
    popup.style.left = `${Math.max(8, left)}px`;
    popup.style.top = `${Math.max(8, top)}px`;
  }
  document.addEventListener('click', event => {
    const target = event.target;
    if (target.closest('dialog[open]')) return;
    if (toggle.contains(target)) {
      if (!mobile.matches) return;
      if (sidebar.classList.contains('is-open')) closeAllPopovers(true);
      else {
        sidebar.classList.add('is-open');
        backdrop.hidden = false;
        toggle.setAttribute('aria-expanded', 'true');
        sidebar.querySelector('#chat-search')?.focus();
      }
      return;
    }
    const summary = target.closest('.chat-menu > summary');
    if (summary) {
      event.preventDefault();
      const menu = summary.parentElement;
      const opening = !menu.open;
      closeChatMenus();
      if (opening) {
        menu.open = true;
        activeMenu = menu;
        summary.setAttribute('aria-expanded', 'true');
        positionMenu(menu);
      }
      return;
    }
    if (target.closest('.chat-menu button')) closeChatMenus();
    else if (!target.closest('.chat-menu')) closeChatMenus();
    if (!sidebar.contains(target)) closeAllPopovers(true);
    else if (target.closest('.history-item, .new-chat-form button')) closeAllPopovers();
  });
  document.addEventListener('keydown', event => {
    if (event.target.closest('dialog[open]')) return;
    if (event.key === 'Escape') {
      if (activeMenu) { event.preventDefault(); closeChatMenus(true); }
      else { closeAllPopovers(true); }
      return;
    }
    if (activeMenu && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const items = [...activeMenu.querySelectorAll('[role="menuitem"]')];
      const current = items.indexOf(document.activeElement);
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (current + (event.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length;
      items[index]?.focus();
    }
    if (event.key === 'Tab' && mobile.matches && sidebar.classList.contains('is-open')) {
      const items = [...sidebar.querySelectorAll('a, button, input, select, summary')].filter(node => !node.disabled && node.getClientRects().length);
      items.push(toggle);
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  document.addEventListener('focusin', event => {
    if (activeMenu && !activeMenu.contains(event.target)) closeChatMenus();
  });
  // Native details changes (including Pin) must keep the accessible state synced.
  sidebar.addEventListener('toggle', event => {
    if (!event.target.matches('.chat-menu')) return;
    event.target.querySelector('summary').setAttribute('aria-expanded', String(event.target.open));
    if (!event.target.open && activeMenu === event.target) activeMenu = null;
  }, true);
  sidebar.querySelector('.history')?.addEventListener('scroll', () => closeChatMenus());
  window.addEventListener('resize', () => { closeChatMenus(); if (!mobile.matches) closeMobileSidebar(); });
  window.addEventListener('pageshow', () => closeAllPopovers());
  closeMobileSidebar();
})();
