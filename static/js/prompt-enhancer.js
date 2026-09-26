"use strict";
(() => {
  const button = document.getElementById("enhance-prompt");
  const form = document.getElementById("question-form");
  if (!button || !form) return;
  const textarea = document.getElementById("id_question");
  const feedback = document.getElementById("enhance-feedback");
  let controller;
  form.addEventListener("submit", () => { if (controller) controller.abort(); });
  button.addEventListener("click", async () => {
    if (button.disabled || form.getAttribute("aria-busy") === "true") return;
    const original = textarea.value;
    if (!original.trim()) { feedback.textContent = "Enter a research idea first."; return; }
    if (original.length > 2000) { feedback.textContent = "Prompt is too long (maximum 2000 characters)."; return; }
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "Enhancing…";
    feedback.textContent = "";
    controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 55000);
    try {
      const response = await fetch(button.dataset.url, {
        method: "POST", credentials: "same-origin", signal: controller.signal,
        headers: {"Content-Type": "application/json", "X-CSRFToken": form.querySelector('[name="csrfmiddlewaretoken"]').value},
        body: JSON.stringify({text: original})
      });
      const data = await response.json();
      if (!response.ok && data.code === "configuration") throw new Error("configuration");
      if (!response.ok || typeof data.enhanced_text !== "string" || !data.enhanced_text.trim()) throw new Error("unavailable");
      if (controller.signal.aborted || form.getAttribute("aria-busy") === "true") return;
      if (textarea.value !== original) { feedback.textContent = "Your text changed during enhancement. Click Enhance Prompt to try again."; return; }
      textarea.value = data.enhanced_text;
      textarea.dispatchEvent(new Event("input", {bubbles: true}));
      textarea.focus();
      feedback.textContent = "Prompt enhanced. Review it, then press Research when ready.";
    } catch (_) {
      feedback.textContent = _.message === "configuration" ? "OpenRouter API key is not configured." : "Prompt enhancement is temporarily unavailable. Your original text is unchanged. Please try again later.";
    } finally {
      clearTimeout(timeout);
      controller = null;
      button.disabled = false;
      button.textContent = label;
    }
  });
})();
