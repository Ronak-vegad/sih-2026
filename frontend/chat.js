/* BIS Chat Interface — chat.js */

(function () {
  "use strict";

  // Auto-detect API base: same origin when served together, else local dev server.
  const API_BASE =
    location.protocol.startsWith("http") && location.port && location.port !== "8000"
      ? "http://localhost:8000"
      : location.protocol.startsWith("http")
      ? location.origin
      : "http://localhost:8000";

  const MIN_QUERY_LEN = 3;
  const MAX_HISTORY   = 10;   // messages (≈5 turns) sent to the backend

  const $ = (id) => document.getElementById(id);

  const messagesEl   = $("chat-messages");
  const inputEl      = $("chat-input");
  const sendBtn      = $("send-btn");
  const sendIcon     = $("send-icon");
  const typingEl     = $("typing-indicator");
  const typingLabel  = $("typing-label");
  const statusDot    = $("status-dot");
  const statusText   = $("status-text");
  const clearBtn     = $("clear-chat-btn");
  const newChatBtn   = $("new-chat-btn");
  const sidebarEl    = $("chat-sidebar");
  const backdropEl   = $("sidebar-backdrop");
  const openSideBtn  = $("sidebar-open-btn");
  const closeSideBtn = $("sidebar-close-btn");
  const charCountEl  = $("char-count");
  const toastEl      = $("toast");
  const statChunks   = $("stat-chunks");
  const statQco      = $("stat-qco");
  const statModel    = $("stat-model");

  const welcomeTemplate = $("welcome-msg")?.cloneNode(true) || null;

  let conversationHistory = [];
  let isLoading  = false;
  let controller = null;   // AbortController for the in-flight request

  const ROUTE_META = {
    structured_lookup: { icon: "grid",    label: "Structured QCO lookup" },
    procedure_rag:     { icon: "list",    label: "Procedure documents" },
    faq_rag:           { icon: "message", label: "FAQ / general guidance" },
    out_of_scope:      { icon: "ban",     label: "Out of scope" },
  };

  // ── Status / health ────────────────────────────────────────────
  function setStatus(state, text) {
    if (statusDot)  statusDot.className = "topbar-dot " + state;
    if (statusText) statusText.textContent = text;
  }

  async function checkHealth() {
    try {
      const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(6000) });
      if (!res.ok) throw new Error("bad status");
      const data = await res.json();

      if (!isLoading) setStatus("online", "Connected");
      if (statChunks) statChunks.textContent = fmt(data.chroma_chunks);
      if (statQco)    statQco.textContent    = fmt(data.qco_table_rows);
      if (statModel) {
        statModel.textContent = data.nvidia_key_set ? "NVIDIA Nemotron 3 Super" : "key not set";
        statModel.title       = data.nvidia_key_set
          ? `${data.llm_model}\n${data.embed_model}`
          : "Set NVIDIA_API_KEY in .env";
      }
    } catch {
      if (!isLoading) setStatus("error", "Backend offline");
      if (statModel) statModel.textContent = "unavailable";
    }
  }

  const fmt = (n) => (typeof n === "number" ? n.toLocaleString("en-IN") : "—");

  // ── Sidebar ───────────────────────────────────────────────────
  function setSidebar(open) {
    sidebarEl?.classList.toggle("open", open);
    if (backdropEl) backdropEl.hidden = !open;
  }
  openSideBtn?.addEventListener("click", () => setSidebar(true));
  closeSideBtn?.addEventListener("click", () => setSidebar(false));
  backdropEl?.addEventListener("click", () => setSidebar(false));

  // ── Delegated starter buttons (survives DOM rebuilds) ─────────
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-q]");
    if (!btn) return;
    if (btn.closest(".chat-sidebar")) setSidebar(false);
    submitQuery(btn.dataset.q);
  });

  // ── Input handling ────────────────────────────────────────────
  function syncComposer() {
    const len = inputEl.value.length;
    if (charCountEl) {
      charCountEl.textContent = `${len}/1000`;
      charCountEl.classList.toggle("near-limit", len > 900);
    }
    sendBtn.disabled = isLoading ? false : inputEl.value.trim().length < MIN_QUERY_LEN;
  }

  inputEl.addEventListener("input", () => {
    inputEl.style.height = "auto";
    inputEl.style.height = Math.min(inputEl.scrollHeight, 168) + "px";
    syncComposer();
  });

  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!sendBtn.disabled) handleSendClick();
    }
  });

  sendBtn.addEventListener("click", handleSendClick);

  function handleSendClick() {
    if (isLoading) {          // acts as a stop button while generating
      controller?.abort();
      return;
    }
    const q = inputEl.value.trim();
    if (q.length < MIN_QUERY_LEN) return;
    inputEl.value = "";
    inputEl.style.height = "auto";
    syncComposer();
    submitQuery(q);
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (sidebarEl?.classList.contains("open")) setSidebar(false);
      else if (isLoading) controller?.abort();
    }
    // "/" focuses the composer
    if (e.key === "/" && document.activeElement !== inputEl) {
      e.preventDefault();
      inputEl.focus();
    }
  });

  // ── Reset conversation ────────────────────────────────────────
  function resetChat() {
    if (isLoading) controller?.abort();
    conversationHistory = [];
    messagesEl.innerHTML = "";
    if (welcomeTemplate) messagesEl.appendChild(welcomeTemplate.cloneNode(true));
    messagesEl.appendChild(typingEl);
    typingEl.hidden = true;
    inputEl.focus();
  }
  clearBtn?.addEventListener("click", resetChat);
  newChatBtn?.addEventListener("click", () => {
    setSidebar(false);
    resetChat();
  });

  // ── Core: submit a query ──────────────────────────────────────
  async function submitQuery(query) {
    if (isLoading) return;
    query = (query || "").trim();
    if (query.length < MIN_QUERY_LEN) return;

    isLoading = true;
    setLoadingUI(true);
    setStatus("loading", "Searching BIS sources…");

    $("welcome-msg")?.remove();
    appendUserMessage(query);

    const historyForApi = conversationHistory.slice(-MAX_HISTORY);

    typingEl.hidden = false;
    if (typingLabel) typingLabel.textContent = "Searching official BIS sources…";
    scrollToBottom();

    const labelTimer = setTimeout(() => {
      if (typingLabel) typingLabel.textContent = "Generating answer…";
    }, 2500);

    controller = new AbortController();

    // Bot message bubble — created once, tokens appended into it
    let botBubble     = null;
    let textContainer = null;
    let metaData      = null;
    let rawText       = "";

    try {
      const res = await fetch(`${API_BASE}/chat/stream`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ query, history: historyForApi, use_nim: true }),
        signal:  controller.signal,
      });

      if (!res.ok) throw new Error(await extractError(res));

      // Hide typing indicator once the stream opens
      typingEl.hidden = true;
      clearTimeout(labelTimer);

      const reader  = res.body.getReader();
      const decoder = new TextDecoder();
      let   buffer  = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop(); // keep incomplete line for next chunk

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          let event;
          try { event = JSON.parse(line.slice(6)); } catch { continue; }

          if (event.type === "meta") {
            // Metadata arrives first — stash it for the full render at the end
            metaData = event;

            // Create the bot bubble immediately so it appears at the right time
            const { bubble } = newMessage("bot");
            botBubble     = bubble;
            textContainer = document.createElement("div");
            textContainer.className = "stream-text";
            bubble.appendChild(textContainer);
            scrollToBottom();

          } else if (event.type === "token" && botBubble) {
            // Append text token — render markdown incrementally
            rawText += event.text;
            textContainer.innerHTML = renderMarkdown(rawText);
            scrollToBottom();

          } else if (event.type === "stripped_is" && metaData) {
            metaData.stripped_is = event.items;

          } else if (event.type === "done") {
            // Stream complete — append metadata row + sources to the bubble
            if (botBubble && metaData) {
              appendMetaAndSources(botBubble, {
                ...metaData,
                answer: rawText,
              });
            }
            break;

          } else if (event.type === "error") {
            throw new Error(event.message || "Stream error");
          }
        }
      }

      if (metaData) {
        conversationHistory.push({ role: "user",      content: query });
        conversationHistory.push({ role: "assistant", content: rawText });
        setStatus("online", metaData.used_nim ? "Answered via NVIDIA NIM" : "Answered");
      }

    } catch (err) {
      typingEl.hidden = true;
      clearTimeout(labelTimer);
      if (err.name === "AbortError") {
        // Show whatever streamed so far, or a cancelled notice
        if (!rawText) appendNotice("Request cancelled.", "info");
        setStatus("online", "Cancelled");
      } else {
        if (!botBubble) appendNotice(err.message, "error");
        setStatus("error", "Error — " + String(err.message).slice(0, 40));
      }
    } finally {
      controller = null;
      isLoading  = false;
      setLoadingUI(false);
      syncComposer();
    }
  }

  async function extractError(res) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) {
        // FastAPI validation errors
        detail = body.detail
          .map((d) => d.msg || JSON.stringify(d))
          .join("; ");
      }
    } catch {
      /* keep default */
    }
    return detail;
  }

  function setLoadingUI(loading) {
    sendBtn.classList.toggle("is-stop", loading);
    sendBtn.setAttribute("aria-label", loading ? "Stop generating" : "Send message");
    sendBtn.title = loading ? "Stop" : "Send";
    if (sendIcon) sendIcon.innerHTML = `<use href="#i-${loading ? "stop" : "send"}"></use>`;
    sendBtn.disabled = loading ? false : inputEl.value.trim().length < MIN_QUERY_LEN;
    inputEl.setAttribute("aria-busy", String(loading));
  }

  // ── Message rendering ─────────────────────────────────────────
  function newMessage(role) {
    const div = document.createElement("div");
    div.className = `message message--${role}`;

    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.innerHTML = window.icon(role === "user" ? "user" : "bot", "icon--sm");

    const bubble = document.createElement("div");
    bubble.className = "message-bubble";

    div.append(avatar, bubble);
    messagesEl.insertBefore(div, typingEl);   // keep typing indicator last
    return { div, bubble };
  }

  function appendUserMessage(text) {
    const { bubble } = newMessage("user");
    const p = document.createElement("p");
    p.textContent = text;
    bubble.appendChild(p);
    scrollToBottom();
  }

  function appendBotMessage(data) {
    const { bubble } = newMessage("bot");
    bubble.innerHTML = renderMarkdown(data.answer || "");

    // Meta row: route, confidence, low-confidence flag, copy
    const meta = document.createElement("div");
    meta.className = "message-meta";

    const route = ROUTE_META[data.route];
    if (route) {
      meta.insertAdjacentHTML(
        "beforeend",
        `<span class="route-badge">${window.icon(route.icon, "icon--xs")}${route.label}</span>`
      );
    }

    if (typeof data.confidence === "number" && data.confidence > 0) {
      const pct  = Math.round(data.confidence * 100);
      const tier = pct >= 60 ? "" : pct >= 40 ? " conf-badge--mid" : " conf-badge--low";
      meta.insertAdjacentHTML(
        "beforeend",
        `<span class="conf-badge${tier}" title="Similarity of the best retrieved passage">
           ${window.icon("check-circle", "icon--xs")}${pct}% source match
         </span>`
      );
    }

    if (data.low_confidence) {
      meta.insertAdjacentHTML(
        "beforeend",
        `<span class="low-confidence-badge">${window.icon("alert-triangle", "icon--xs")}Low confidence — verify at bis.gov.in</span>`
      );
    }

    if (Array.isArray(data.stripped_is) && data.stripped_is.length) {
      meta.insertAdjacentHTML(
        "beforeend",
        `<span class="low-confidence-badge" title="Removed by the anti-hallucination guardrail">
           ${window.icon("shield-check", "icon--xs")}${data.stripped_is.length} unverified IS number(s) removed
         </span>`
      );
    }

    const copyBtn = document.createElement("button");
    copyBtn.className = "copy-btn";
    copyBtn.type = "button";
    copyBtn.innerHTML = window.icon("copy", "icon--xs") + "<span>Copy</span>";
    copyBtn.addEventListener("click", () => copyAnswer(data.answer, copyBtn));
    meta.appendChild(copyBtn);

    bubble.appendChild(meta);

    // Sources
    if (Array.isArray(data.sources) && data.sources.length) {
      const wrap = document.createElement("div");
      wrap.className = "message-sources";
      wrap.innerHTML = `<div class="sources-label">${window.icon("file-badge", "icon--xs")}Sources</div>`;

      const tags = document.createElement("div");
      tags.className = "source-tags";

      data.sources.forEach((src) => {
        const hasUrl = !!src.url;
        const el = document.createElement(hasUrl ? "a" : "span");
        el.className = "source-tag";
        if (hasUrl) {
          el.href = src.url;
          el.target = "_blank";
          el.rel = "noopener noreferrer";
          el.title = src.url;
        }
        const iconName = (src.category || "").toLowerCase().includes("pdf")
          ? "file-badge"
          : hasUrl
          ? "globe"
          : "file-badge";
        el.innerHTML = window.icon(iconName, "icon--xs");
        const label = document.createElement("span");
        label.textContent = src.title || src.url || "Untitled source";
        el.appendChild(label);
        tags.appendChild(el);
      });

      wrap.appendChild(tags);
      bubble.appendChild(wrap);
    }

    scrollToBottom();
  }

  function appendNotice(text, kind) {
    const { bubble } = newMessage("bot");
    if (kind === "error") bubble.classList.add("message-bubble--error");
    const iconName = kind === "error" ? "alert-triangle" : "info";
    bubble.innerHTML =
      `<p>${window.icon(iconName, "icon--sm")} <strong>${escapeHtml(text)}</strong></p>` +
      (kind === "error"
        ? `<p style="font-size:0.82rem;color:var(--muted);margin-top:0.4rem;">
             Check that the backend is running (<code>uvicorn backend.main:app --reload</code>)
             and reachable at <code>${escapeHtml(API_BASE)}</code>.
           </p>`
        : "");
    scrollToBottom();
  }

  async function copyAnswer(text, btn) {
    try {
      await navigator.clipboard.writeText(text || "");
      btn.innerHTML = window.icon("check", "icon--xs") + "<span>Copied</span>";
      showToast("Answer copied to clipboard");
      setTimeout(() => {
        btn.innerHTML = window.icon("copy", "icon--xs") + "<span>Copy</span>";
      }, 1800);
    } catch {
      showToast("Copy failed — select the text manually");
    }
  }

  let toastTimer = null;
  function showToast(msg) {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toastEl.hidden = true), 2200);
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    });
  }

  // ── Utilities ─────────────────────────────────────────────────
  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /** Minimal, safe markdown renderer (input is escaped first). */
  function renderMarkdown(text) {
    let html = escapeHtml(text.trim());

    // Fenced code blocks first, so their content is not touched by later rules
    const codeBlocks = [];
    html = html.replace(/```[\w]*\n([\s\S]*?)```/g, (_, code) => {
      codeBlocks.push(code);
      return `\u0000CODE${codeBlocks.length - 1}\u0000`;
    });

    // Markdown tables → real tables
    html = html.replace(
      /(^\|.+\|[ \t]*\n\|[ \t]*:?-{2,}.*\n(?:\|.*\|[ \t]*\n?)*)/gm,
      (block) => renderTable(block)
    );

    // Headings
    html = html.replace(/^#{4,}\s+(.+)$/gm, "<h4>$1</h4>");
    html = html.replace(/^###\s+(.+)$/gm,   "<h4>$1</h4>");
    html = html.replace(/^##\s+(.+)$/gm,    "<h3>$1</h3>");
    html = html.replace(/^#\s+(.+)$/gm,     "<h3>$1</h3>");

    // Horizontal rule
    html = html.replace(/^\s*(?:---|\*\*\*|___)\s*$/gm, "<hr>");

    // Blockquote
    html = html.replace(/^&gt;\s?(.*)$/gm, "<blockquote>$1</blockquote>");

    // Emphasis
    html = html.replace(/\*\*\*([^*\n]+)\*\*\*/g, "<strong><em>$1</em></strong>");
    html = html.replace(/\*\*([^*\n]+)\*\*/g,     "<strong>$1</strong>");
    html = html.replace(/__([^_\n]+)__/g,         "<strong>$1</strong>");
    html = html.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s.,;:!?)]|$)/g, "$1<em>$2</em>");

    // Inline code
    html = html.replace(/`([^`\n]+)`/g, "<code>$1</code>");

    // Links: [text](url) and bare URLs
    html = html.replace(
      /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
    );
    html = html.replace(
      /(^|[\s(])(https?:\/\/[^\s<)]+)/g,
      '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>'
    );

    // Lists (block-aware: consecutive item lines become one list)
    html = html.replace(/(?:^[ \t]*(?:[-*•])[ \t]+.+(?:\n|$))+/gm, (block) => wrapList(block, "ul"));
    html = html.replace(/(?:^[ \t]*\d+[.)][ \t]+.+(?:\n|$))+/gm,   (block) => wrapList(block, "ol"));

    // Paragraphs
    const out = html
      .split(/\n{2,}/)
      .map((block) => {
        block = block.trim();
        if (!block) return "";
        if (/^<(h[2-4]|ul|ol|hr|blockquote|div|table|pre)/.test(block)) return block;
        return `<p>${block.replace(/\n/g, "<br>")}</p>`;
      })
      .join("\n");

    // Restore code blocks
    return out.replace(/\u0000CODE(\d+)\u0000/g, (_, i) => `<pre><code>${codeBlocks[i]}</code></pre>`);
  }

  function wrapList(block, tag) {
    const items = block
      .trim()
      .split("\n")
      .map((line) => line.replace(/^[ \t]*(?:[-*•]|\d+[.)])[ \t]+/, "").trim())
      .filter(Boolean)
      .map((item) => `<li>${item}</li>`)
      .join("");
    return `<${tag}>${items}</${tag}>`;
  }

  function renderTable(block) {
    const rows = block.trim().split("\n").filter((r) => r.trim());
    if (rows.length < 2) return block;

    const cells = (row) =>
      row.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

    const head = cells(rows[0]);
    const body = rows.slice(2).map(cells);

    const thead = `<thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead>`;
    const tbody = `<tbody>${body
      .map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`)
      .join("")}</tbody>`;

    return `<div class="md-table-wrap"><table>${thead}${tbody}</table></div>`;
  }

  // ── Init ──────────────────────────────────────────────────────
  syncComposer();
  checkHealth();
  setInterval(checkHealth, 30000);
  inputEl.focus();
})();
