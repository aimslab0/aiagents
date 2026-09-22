"use strict";
(() => {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  let preference = "system";
  try { preference = localStorage.getItem("research-theme") || "system"; } catch (_) {}
  if (!["light", "dark", "system"].includes(preference)) preference = "system";
  const apply = () => {
    document.documentElement.dataset.bsTheme = preference === "system" ? (media.matches ? "dark" : "light") : preference;
  };
  apply();
  media.addEventListener("change", apply);
  document.addEventListener("DOMContentLoaded", () => {
    const select = document.getElementById("theme-select");
    if (!select) return;
    select.value = preference;
    select.addEventListener("change", () => {
      preference = select.value;
      try { localStorage.setItem("research-theme", preference); } catch (_) {}
      apply();
    });
  });
})();
