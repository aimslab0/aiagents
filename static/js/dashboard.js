"use strict";

document.addEventListener("click", event => {
  const link = event.target.closest?.("[data-focus-research], [data-open-research-details]");
  if (!link) return;
  const target = document.getElementById(link.hash.slice(1));
  if (!target) return;
  event.preventDefault();
  const sidebar = document.getElementById("sidebar");
  if (sidebar?.classList.contains("is-open")) document.getElementById("sidebar-toggle").click();
  if (target.tagName === "DETAILS") target.open = true;
  target.scrollIntoView({block: "center"});
  if (target.tagName === "DETAILS") target.querySelector("summary")?.focus();
  else target.focus();
});

function autoResizeResearchTextarea() {
  const textarea = document.getElementById("id_question");
  if (!textarea) return;
  const style = window.getComputedStyle(textarea);
  const minimum = parseFloat(style.minHeight) || 80;
  const maximum = parseFloat(style.maxHeight) || 260;
  const border = (parseFloat(style.borderTopWidth) || 0) + (parseFloat(style.borderBottomWidth) || 0);
  textarea.style.height = "0px";
  const needed = textarea.scrollHeight + border;
  textarea.style.height = `${Math.min(maximum, Math.max(minimum, needed))}px`;
  textarea.style.overflowY = needed > maximum ? "auto" : "hidden";
}

// The enhancer dispatches input after replacing the value. Delegation also covers
// replacement form elements without rebinding their listeners.
document.addEventListener("input", (event) => {
  if (event.target.id === "id_question") autoResizeResearchTextarea();
});
window.addEventListener("resize", autoResizeResearchTextarea);
window.addEventListener("pageshow", autoResizeResearchTextarea);
autoResizeResearchTextarea();
document.addEventListener("keydown", (event) => {
  if (event.target.id === "chat-search" && event.key === "Enter") { event.preventDefault(); return; }
  if (event.target.id !== "id_question" || event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
  if (!window.matchMedia("(min-width: 761px) and (hover: hover) and (pointer: fine)").matches) return;
  event.preventDefault();
  const form = event.target.form;
  if (event.target.value.trim() && form && form.getAttribute("aria-busy") !== "true") form.requestSubmit();
});

(() => {
  const mode = document.getElementById("id_research_mode");
  const judge = document.getElementById("id_deep_synthesizer");
  const control = document.getElementById("deep-synthesizer-control");
  const optionsData = document.getElementById("deep-synthesizer-options");
  if (!mode || !judge || !control || !optionsData) return;
  const options = JSON.parse(optionsData.textContent);
  const update = () => {
    const visible = mode.value === "deep" && document.getElementById("research-options").dataset.freeTest !== "true";
    control.hidden = !visible;
    judge.disabled = !visible;
    document.getElementById("deep-synthesizer-warning").textContent = visible ? (options.find(option => option.key === judge.value)?.warning || "") : "";
  };
  mode.addEventListener("change", update);
  judge.addEventListener("change", update);
  window.addEventListener("pageshow", update);
  update();
})();

document.querySelectorAll(".deep-judge-select").forEach((select) => {
  const update = () => { document.getElementById(select.getAttribute("aria-describedby")).textContent = select.selectedOptions[0]?.dataset.warning || ""; };
  select.addEventListener("change", update);
  update();
});

document.querySelectorAll(".retry-form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (document.querySelector('.retry-form[aria-busy="true"]')) { event.preventDefault(); return; }
    document.querySelectorAll('.retry-form button[type="submit"]').forEach((button) => { button.disabled = true; });
    form.setAttribute("aria-busy", "true");
    const progress = form.querySelector(".retry-progress");
    if (progress) progress.hidden = false;
  });
});
window.addEventListener("pageshow", () => {
  document.querySelectorAll('.retry-form button[type="submit"]').forEach((button) => { button.disabled = false; });
  document.querySelectorAll(".retry-form").forEach((form) => form.removeAttribute("aria-busy"));
  document.querySelectorAll(".retry-progress").forEach((progress) => { progress.hidden = true; });
});

document.documentElement.classList.add("js-enabled");
const conversation = document.getElementById("conversation");
const finalAnswers = document.querySelectorAll(".research-snapshot");
if (finalAnswers.length) {
  finalAnswers[finalAnswers.length - 1].scrollIntoView({ block: "start" });
} else if (conversation && conversation.querySelector(".message")) {
  conversation.scrollTop = conversation.scrollHeight;
}

const questionForm = document.getElementById("question-form");
if (questionForm) {
  const progress = document.getElementById("research-progress");
  questionForm.addEventListener("submit", (event) => {
    if (questionForm.getAttribute("aria-busy") === "true") { event.preventDefault(); return; }
    const textarea = questionForm.querySelector("#id_question");
    if (!textarea.value.trim()) { event.preventDefault(); textarea.focus(); return; }
    const button = questionForm.querySelector('button[type="submit"]');
    button.disabled = true;
    button.textContent = "Researching…";
    questionForm.setAttribute("aria-busy", "true");
    if (progress) progress.hidden = false;
  });
  window.addEventListener("pageshow", () => {
    const button = questionForm.querySelector('button[type="submit"]');
    button.disabled = false;
    button.textContent = "Research ↑";
    questionForm.removeAttribute("aria-busy");
    if (progress) progress.hidden = true;
  });
}

const chatSearch = document.getElementById("chat-search");
if (chatSearch) chatSearch.addEventListener("input", () => {
  let matches = 0;
  document.querySelectorAll(".history-row").forEach((row) => {
    row.hidden = !row.dataset.chatTitle.toLocaleLowerCase().includes(chatSearch.value.trim().toLocaleLowerCase());
    if (!row.hidden) matches++;
  });
  document.getElementById("no-chat-matches").hidden = matches > 0 || !chatSearch.value;
});

const dialog = document.getElementById("chat-dialog");
if (dialog) {
  const form = document.getElementById("chat-management-form");
  const input = document.getElementById("chat-input");
  const label = document.getElementById("chat-input-label");
  const confirmation = document.getElementById("chat-confirm");
  document.querySelectorAll("[data-chat-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.dataset.chatAction;
      form.action = button.dataset.url;
      input.hidden = label.hidden = action === "delete";
      input.disabled = action === "delete";
      input.name = action === "clear" ? "confirmation" : "title";
      input.value = action === "rename" ? button.dataset.title : "";
      input.setCustomValidity("");
      confirmation.value = action === "delete" ? button.dataset.chatId : "";
      label.textContent = action === "clear" ? "Type DELETE ALL CHATS to confirm" : "Research title";
      document.getElementById("chat-dialog-title").textContent = action === "rename" ? "Rename research" : action === "delete" ? "Delete research?" : "Clear all chats?";
      document.getElementById("chat-dialog-description").textContent = action === "delete" ? `Delete “${button.dataset.title}” and all its related research? This cannot be undone.` : action === "clear" ? "All chats, research, sources and saved results will be permanently deleted." : "Choose a title for this saved research.";
      document.getElementById("chat-confirm-button").textContent = action === "rename" ? "Save title" : "Confirm deletion";
      dialog.showModal();
      (action === "delete" ? document.getElementById("chat-cancel") : input).focus();
    });
  });
  input.addEventListener("input", () => input.setCustomValidity(""));
  form.addEventListener("submit", (event) => {
    if (input.name === "confirmation" && input.value !== "DELETE ALL CHATS") {
      event.preventDefault();
      input.setCustomValidity("Type DELETE ALL CHATS exactly to confirm.");
      input.reportValidity();
    }
  });
  document.getElementById("chat-cancel").addEventListener("click", () => dialog.close());
}

document.querySelectorAll("[data-copy]").forEach((button) => button.addEventListener("click", async () => {
  const target = document.getElementById(button.dataset.copy);
  const feedback = document.getElementById("ui-feedback");
  try {
    const content = target.cloneNode(true);
    content.querySelectorAll("p, .source-card").forEach((node) => node.append("\n"));
    await navigator.clipboard.writeText(content.textContent.trim());
    feedback.textContent = "Copied to clipboard.";
  } catch (_) { feedback.textContent = "Clipboard unavailable. Expand and select the text to copy it."; }
}));

function revealAnchor() {
  const target = document.getElementById(window.location.hash.slice(1));
  if (!target) return;
  for (let node = target; node; node = node.parentElement) {
    if (node.tagName === "DETAILS") node.open = true;
  }
  target.scrollIntoView({ block: "start" });
}
window.addEventListener("hashchange", revealAnchor);
if (window.location.hash) revealAnchor();
