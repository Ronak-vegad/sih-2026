/* ============================================================
   BIS Intelligent Assistant — Inline SVG icon sprite
   ------------------------------------------------------------
   No CDN, no dependency, works fully offline (important for a
   live SIH demo). Include with:

       <script src="icons.js"></script>

   as the FIRST element inside <body> so the sprite exists in the
   DOM before any <use> reference is parsed.

   Usage in HTML:  <svg class="icon"><use href="#i-zap"/></svg>
   Usage in JS:    element.innerHTML = icon("zap", "icon--sm")
   ============================================================ */

(function () {
  const S = (id, body) =>
    `<symbol id="i-${id}" viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
    `stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${body}</symbol>`;

  const SPRITE = `
<svg id="bis-icon-sprite" width="0" height="0" aria-hidden="true" focusable="false"
     style="position:absolute;width:0;height:0;overflow:hidden">
${[
    // ── Brand / navigation ──────────────────────────────────
    S("home",      `<path d="m3 10 9-7 9 7v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 21v-6h6v6"/>`),
    S("menu",      `<path d="M3 6h18"/><path d="M3 12h18"/><path d="M3 18h18"/>`),
    S("x",         `<path d="M18 6 6 18"/><path d="m6 6 12 12"/>`),
    S("arrow-right", `<path d="M4 12h15"/><path d="m13 6 6 6-6 6"/>`),
    S("external", `<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>`),
    S("chevron-down", `<path d="m6 9 6 6 6-6"/>`),
    S("plus",      `<path d="M12 5v14"/><path d="M5 12h14"/>`),

    // ── Core BIS domain ─────────────────────────────────────
    S("zap",       `<path d="M13 2 4 13.5h6.5L9.5 22 20 10.5h-6.5z"/>`),
    S("clipboard-check", `<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="m9 14 2 2 4-4"/>`),
    S("gem",       `<path d="M6 3h12l4 6-10 12L2 9z"/><path d="M2 9h20"/><path d="M11 3 8 9l4 12 4-12-3-6"/>`),
    S("file-badge", `<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><circle cx="12" cy="14" r="2.4"/><path d="M10.4 16.1 9.9 21l2.1-1.2 2.1 1.2-.5-4.9"/>`),
    S("flask",     `<path d="M9 3h6"/><path d="M10 3v5.6L4.9 18a2 2 0 0 0 1.7 3h10.8a2 2 0 0 0 1.7-3L14 8.6V3"/><path d="M7 15h10"/>`),
    S("users",     `<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.1a4 4 0 0 1 0 7.8"/>`),
    S("hard-hat",  `<path d="M2 18a1 1 0 0 0 1 1h18a1 1 0 0 0 1-1v-1.5a1 1 0 0 0-1-1H3a1 1 0 0 0-1 1z"/><path d="M10 10.5V5a2 2 0 0 1 4 0v5.5"/><path d="M5 15.5v-3a7 7 0 0 1 5-6.7"/><path d="M14 5.8a7 7 0 0 1 5 6.7v3"/>`),
    S("building",  `<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 6h2"/><path d="M13 6h2"/><path d="M9 10h2"/><path d="M13 10h2"/><path d="M9 14h2"/><path d="M13 14h2"/><path d="M10 22v-4h4v4"/>`),
    S("lightbulb", `<path d="M9.5 18h5"/><path d="M10 21.5h4"/><path d="M15.1 14.6a6 6 0 1 0-6.2 0A3.4 3.4 0 0 1 10 17v1h4v-1a3.4 3.4 0 0 1 1.1-2.4z"/>`),
    S("star",      `<path d="m12 2.5 2.9 6 6.6 1-4.8 4.6 1.1 6.5L12 17.5 6.2 20.6l1.1-6.5L2.5 9.5l6.6-1z"/>`),
    S("file-pen",  `<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h5"/><path d="M14 2v6h6"/><path d="M18.4 12.6a2 2 0 0 1 2.9 2.9L16.2 20.6 13 21.5l.9-3.2z"/>`),
    S("list",      `<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3.5 6h.01"/><path d="M3.5 12h.01"/><path d="M3.5 18h.01"/>`),
    S("scale",     `<path d="M12 3v18"/><path d="M7 21h10"/><path d="M5 7h14"/><path d="M5 7 2 13h6z"/><path d="M19 7l-3 6h6z"/>`),

    // ── Trust / status ──────────────────────────────────────
    S("shield-check", `<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>`),
    S("check",     `<path d="m4.5 12.5 5 5 10-11"/>`),
    S("check-circle", `<circle cx="12" cy="12" r="9"/><path d="m8.5 12.5 2.2 2.2 4.8-5.2"/>`),
    S("alert-triangle", `<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4"/><path d="M12 17h.01"/>`),
    S("info",      `<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>`),
    S("ban",       `<circle cx="12" cy="12" r="9"/><path d="m5.6 5.6 12.8 12.8"/>`),
    S("sparkles",  `<path d="m12 3 1.7 4.6L18.3 9.3l-4.6 1.7L12 15.6l-1.7-4.6L5.7 9.3l4.6-1.7z"/><path d="m18.5 15 .8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>`),

    // ── Pipeline / tech ─────────────────────────────────────
    S("search",    `<circle cx="11" cy="11" r="7"/><path d="m20 20-3.7-3.7"/>`),
    S("filter",    `<path d="M21.5 3.5H2.5l7.5 9v6.5l4 2v-8.5z"/>`),
    S("layers",    `<path d="m12 2.5 9.5 5-9.5 5-9.5-5z"/><path d="m2.5 12 9.5 5 9.5-5"/><path d="m2.5 17 9.5 5 9.5-5"/>`),
    S("database",  `<ellipse cx="12" cy="5.5" rx="8.5" ry="3"/><path d="M3.5 5.5v13c0 1.7 3.8 3 8.5 3s8.5-1.3 8.5-3v-13"/><path d="M3.5 12c0 1.7 3.8 3 8.5 3s8.5-1.3 8.5-3"/>`),
    S("cpu",       `<rect x="5" y="5" width="14" height="14" rx="2.5"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 2v3"/><path d="M15 2v3"/><path d="M9 19v3"/><path d="M15 19v3"/><path d="M2 9h3"/><path d="M2 15h3"/><path d="M19 9h3"/><path d="M19 15h3"/>`),
    S("grid",      `<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M3 15h18"/><path d="M9 3v18"/><path d="M15 3v18"/>`),
    S("route",     `<circle cx="6" cy="19" r="2.5"/><circle cx="18" cy="5" r="2.5"/><path d="M15.5 5H11a4 4 0 0 0 0 8h2a4 4 0 0 1 0 8H8.5"/>`),

    // ── Chat UI ─────────────────────────────────────────────
    S("bot",       `<rect x="3.5" y="8" width="17" height="12.5" rx="3.5"/><path d="M12 4.5V8"/><circle cx="12" cy="3" r="1.2"/><path d="M9 13h.01"/><path d="M15 13h.01"/><path d="M9.8 16.8h4.4"/>`),
    S("user",      `<circle cx="12" cy="8" r="4"/><path d="M4.5 21a7.5 7.5 0 0 1 15 0"/>`),
    S("send",      `<path d="M21.5 2.5 11 13"/><path d="M21.5 2.5 15 21.5 11 13 2.5 9z"/>`),
    S("stop",      `<rect x="6" y="6" width="12" height="12" rx="2.5"/>`),
    S("rotate",    `<path d="M20.5 12a8.5 8.5 0 1 1-2.6-6.1"/><path d="M20.5 3.5V9H15"/>`),
    S("copy",      `<rect x="9" y="9" width="12" height="12" rx="2.5"/><path d="M5.5 15H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v.5"/>`),
    S("message",   `<path d="M21 11.6a8.4 8.4 0 0 1-9 8.4 9.1 9.1 0 0 1-3.9-.9L3 21l1.9-5.1A8.4 8.4 0 0 1 4 11.6 8.5 8.5 0 0 1 12.4 3 8.5 8.5 0 0 1 21 11.6z"/>`),
    S("globe",     `<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a15 15 0 0 1 0 18"/><path d="M12 3a15 15 0 0 0 0 18"/>`),
    S("link",      `<path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7L11 6"/><path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7L13 18"/>`),
    S("phone",     `<path d="M21.5 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.4 19.4 0 0 1-6-6A19.8 19.8 0 0 1 1.6 4.2 2 2 0 0 1 3.6 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.6a2 2 0 0 1-.5 2.1L7.6 9.6a16 16 0 0 0 6 6l1.2-1.2a2 2 0 0 1 2.1-.5c.8.3 1.7.6 2.6.7a2 2 0 0 1 1.7 2z"/>`),
    S("clock",     `<circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 2"/>`),
    S("keyboard",  `<rect x="2" y="6" width="20" height="12" rx="2.5"/><path d="M6 10h.01"/><path d="M10 10h.01"/><path d="M14 10h.01"/><path d="M18 10h.01"/><path d="M7 14h10"/>`),
  ].join("\n")}
</svg>`;

  // Inject immediately so <use> references resolve on first paint.
  function inject() {
    if (document.getElementById("bis-icon-sprite")) return;
    (document.body || document.documentElement).insertAdjacentHTML("afterbegin", SPRITE);
  }

  if (document.body) inject();
  else document.addEventListener("DOMContentLoaded", inject, { once: true });

  /** Build icon markup for JS-rendered content. */
  window.icon = function (name, extraClass) {
    return (
      `<svg class="icon${extraClass ? " " + extraClass : ""}" aria-hidden="true" ` +
      `focusable="false"><use href="#i-${name}"></use></svg>`
    );
  };
})();
