"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  folders: [],
  currentFolder: "inbox",
  messages: [],
  currentMessage: null,
  syncing: false,
  view: localStorage.getItem("mailbox.view") === "board" ? "board" : "list",
};

// ---------- helpers ----------
function fmtDate(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const now = new Date();
    const sameDay =
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate();
    if (sameDay) {
      return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    }
    const sameYear = d.getFullYear() === now.getFullYear();
    return sameYear
      ? d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
      : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  } catch (_e) {
    return iso;
  }
}

function fmtSize(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

async function api(path, opts) {
  const res = await fetch(path, { ...opts, headers: { ...opts?.headers, "X-Mailbox-Token": document.querySelector('meta[name="mailbox-token"]').content } });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    let code = null;
    try {
      const j = await res.json();
      msg = j.error || msg;
      code = j.code;
    } catch (_e) {}
    const error = new Error(msg);
    error.code = code;
    throw error;
  }
  return res.json();
}

// Message markup cannot create active elements or request external resources.
function renderMarkdown(md) {
  try {
    return DOMPurify.sanitize(marked.parse(md, { breaks: true, gfm: true }), {
      ALLOWED_TAGS: ["p", "br", "strong", "em", "del", "blockquote", "pre", "code", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "table", "thead", "tbody", "tr", "th", "td", "a"],
      ALLOWED_ATTR: ["href", "title"],
      ALLOWED_URI_REGEXP: /^(?:https?:|mailto:)/i,
      ALLOW_DATA_ATTR: false,
      ALLOW_ARIA_ATTR: false,
    });
  } catch (_e) {
    return `<pre>${escapeHtml(md)}</pre>`;
  }
}

function bindAttachments(container) {
  container.querySelectorAll(".attachment-item").forEach((link) => {
    link.addEventListener("click", async (event) => {
      event.preventDefault();
      try {
        const response = await fetch(link.dataset.url, {
          headers: { "X-Mailbox-Token": document.querySelector('meta[name="mailbox-token"]').content },
        });
        if (!response.ok) throw new Error("Attachment unavailable.");
        const objectUrl = URL.createObjectURL(await response.blob());
        const download = document.createElement("a");
        download.href = objectUrl;
        download.download = link.dataset.filename.replace(/[\\/:*?"<>|\x00-\x1f]/g, "_") || "attachment";
        document.body.appendChild(download);
        download.click();
        download.remove();
        setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      } catch (error) { toast(error.message, "error"); }
    });
  });
}

function toast(message, type = "info") {
  const el = document.createElement("div");
  el.className = "toast" + (type === "error" ? " error" : "");
  el.textContent = message;
  $("toast-root").appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

function setStatus(text) {
  $("status").textContent = text || "";
}

// ---------- folder pane ----------
async function loadFolders(preserveSelection = true) {
  state.folders = await api("/api/folders");
  renderFolders();
  if (preserveSelection) {
    const has = state.folders.some((f) => f.path === state.currentFolder);
    if (!has && state.folders.length) {
      state.currentFolder = state.folders[0].path;
      await loadMessages(state.currentFolder);
    }
  }
}

function renderFolders() {
  const list = $("folder-list");
  list.innerHTML = "";
  for (const f of state.folders) {
    const depth = f.depth || 0;
    const li = document.createElement("li");
    li.className = "folder-item" + (f.path === state.currentFolder ? " selected" : "");
    li.dataset.path = f.path;
    li.style.paddingLeft = (20 + depth * 14) + "px";
    li.innerHTML = `
      <span class="name">${escapeHtml(f.name)}</span>
      <div class="row-actions">
        <button class="row-action-btn" type="button" data-action="open" title="Open in File Explorer" aria-label="Open in File Explorer">📂</button>
        <button class="row-action-btn" type="button" data-action="subfolder" title="New subfolder" aria-label="New subfolder">＋</button>
      </div>
      <span class="count">${f.count}</span>
    `;
    li.addEventListener("click", (e) => {
      if (e.target.closest(".row-action-btn")) return;
      selectFolder(f.path);
    });
    li.querySelector('[data-action="open"]').addEventListener("click", (e) => {
      e.stopPropagation();
      openInExplorer(f.path);
    });
    li.querySelector('[data-action="subfolder"]').addEventListener("click", (e) => {
      e.stopPropagation();
      createSubfolder(f.path);
    });
    li.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      li.classList.add("drop-target");
    });
    li.addEventListener("dragleave", () => li.classList.remove("drop-target"));
    li.addEventListener("drop", async (e) => {
      e.preventDefault();
      li.classList.remove("drop-target");
      const path = e.dataTransfer.getData("text/x-mailbox-path");
      if (path) await moveMessage(path, f.path);
    });
    list.appendChild(li);
  }
}

async function openInExplorer(path) {
  try {
    await api("/api/open-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
  } catch (e) {
    toast(`Couldn't open folder: ${e.message}`, "error");
  }
}

async function createSubfolder(parentPath) {
  const name = prompt(`New folder inside "${parentPath}":`);
  if (!name) return;
  try {
    await api("/api/folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim(), parent: parentPath }),
    });
    toast(`Created ${parentPath}/${name}`);
    await loadFolders();
  } catch (e) {
    toast(`Couldn't create folder: ${e.message}`, "error");
  }
}

async function selectFolder(path) {
  state.currentFolder = path;
  state.currentMessage = null;
  renderFolders();
  $("messages-header").textContent = path;
  renderReader(null);
  await loadMessages(path);
}

// ---------- message list ----------
async function loadMessages(folder) {
  try {
    state.messages = await api(`/api/messages?folder=${encodeURIComponent(folder)}`);
  } catch (e) {
    state.messages = [];
    toast(`Couldn't load ${folder}: ${e.message}`, "error");
  }
  renderMessages();
}

function renderMessages() {
  const list = $("message-list");
  list.innerHTML = "";
  if (!state.messages.length) {
    list.innerHTML = `<li class="empty-list">No messages here yet.</li>`;
    return;
  }
  for (const m of state.messages) {
    const li = document.createElement("li");
    const sel = state.currentMessage && state.currentMessage.path === m.path;
    li.className = "message-item" +
                   (m.unread ? " unread" : "") +
                   (sel ? " selected" : "");
    li.dataset.path = m.path;
    li.draggable = true;
    const fromName = displayName(m.from);
    li.innerHTML = `
      <div class="row1">
        <span class="from">${escapeHtml(fromName)}</span>
        <span class="date">${escapeHtml(fmtDate(m.date))}</span>
      </div>
      <div class="subject">${m.has_attachments ? '<span class="attach-icon">📎</span>' : ""}${escapeHtml(m.subject)}</div>
      <div class="snippet">${escapeHtml(m.snippet)}</div>
    `;
    li.addEventListener("click", () => selectMessage(m.path));
    li.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/x-mailbox-path", m.path);
      e.dataTransfer.effectAllowed = "move";
      li.classList.add("dragging");
    });
    li.addEventListener("dragend", () => li.classList.remove("dragging"));
    li.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      showCtxMenu(e.clientX, e.clientY, m);
    });
    list.appendChild(li);
  }
}

function displayName(addr) {
  if (!addr) return "(unknown sender)";
  return addr;
}

// ---------- reader pane ----------
async function selectMessage(path) {
  try {
    const data = await api(`/api/message?path=${encodeURIComponent(path)}`);
    state.currentMessage = data;
    renderMessages();
    renderReader(data);
  } catch (e) {
    toast(`Couldn't open message: ${e.message}`, "error");
  }
}

function renderReader(msg) {
  const r = $("reader");
  if (!msg) {
    r.innerHTML = `<div class="reader-empty">Select a message.</div>`;
    return;
  }
  const fm = msg.frontmatter || {};
  const to = Array.isArray(fm.to) ? fm.to.join(", ") : (fm.to || "");
  const cc = Array.isArray(fm.cc) ? fm.cc.join(", ") : (fm.cc || "");
  const md = msg.body || "";
  const bodyHtml = renderMarkdown(md);
  const attachHtml = (msg.attachments || []).map((a) => {
    const id = fm.id;
    const url = `/api/attachment/${encodeURIComponent(id)}/${encodeURIComponent(a.filename)}`;
    return `<a class="attachment-item" href="#" data-url="${escapeHtml(url)}" data-filename="${escapeHtml(a.filename)}">📎 ${escapeHtml(a.filename)} <span class="size">${fmtSize(a.size)}</span></a>`;
  }).join("");

  // Hide blank meta rows so plain notes (no email frontmatter) render clean
  const metaRows = [
    fm.from ? `<div><span class="label">From:</span> <span class="value">${escapeHtml(fm.from)}</span></div>` : "",
    to ? `<div><span class="label">To:</span> <span class="value">${escapeHtml(to)}</span></div>` : "",
    cc ? `<div><span class="label">Cc:</span> <span class="value">${escapeHtml(cc)}</span></div>` : "",
    fm.date ? `<div><span class="label">Date:</span> <span class="value">${escapeHtml(fm.date)}</span></div>` : "",
  ].filter(Boolean).join("");

  const isEmail = Boolean(fm.from || fm.id);

  r.innerHTML = `
    <div class="reader-header">
      <h2>${escapeHtml(fm.subject || msg.path?.split("/").pop() || "(no subject)")}</h2>
      ${metaRows ? `<div class="reader-meta">${metaRows}</div>` : ""}
      <div class="reader-actions">
        ${isEmail ? `<button class="btn btn-primary" id="reply-btn">Reply</button>` : ""}
        <button class="btn" id="open-file-btn" title="Open in default editor">Open in editor</button>
      </div>
    </div>
    <div class="reader-body">${bodyHtml}</div>
    ${attachHtml ? `<div class="attachments"><h3>Attachments</h3>${attachHtml}</div>` : ""}
  `;
  if (isEmail) {
    $("reply-btn").addEventListener("click", () => openCompose({ replyTo: msg }));
  }
  bindAttachments(r);
  $("open-file-btn").addEventListener("click", () => openFileInEditor(msg.path));
}

async function openFileInEditor(path) {
  try {
    await api("/api/open-file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
  } catch (e) {
    toast(`Couldn't open file: ${e.message}`, "error");
  }
}

// ---------- context menu ----------
function showCtxMenu(x, y, msg) {
  const menu = $("ctx-menu");
  menu.innerHTML = "";
  const header = document.createElement("div");
  header.className = "ctx-header";
  header.textContent = "Move to";
  menu.appendChild(header);
  for (const f of state.folders) {
    if (f.path === state.currentFolder) continue;
    const item = document.createElement("div");
    item.className = "ctx-item";
    item.textContent = f.name;
    item.addEventListener("click", async () => {
      hideCtxMenu();
      await moveMessage(msg.path, f.path);
    });
    menu.appendChild(item);
  }
  if (state.folders.length <= 1) {
    const empty = document.createElement("div");
    empty.className = "ctx-item";
    empty.style.color = "#80868b";
    empty.textContent = "No other folders. Create one first.";
    menu.appendChild(empty);
  }
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
  menu.hidden = false;
}

function hideCtxMenu() {
  $("ctx-menu").hidden = true;
}

document.addEventListener("click", (e) => {
  if (!e.target.closest("#ctx-menu")) hideCtxMenu();
});

// ---------- actions ----------
async function moveMessage(path, target) {
  try {
    await api("/api/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, target_folder: target }),
    });
    toast(`Moved to ${target}`);
    if (state.currentMessage && state.currentMessage.path === path) {
      state.currentMessage = null;
      renderReader(null);
    }
    await loadFolders();
    if (state.view === "board") {
      await renderBoard();
    } else {
      await loadMessages(state.currentFolder);
    }
  } catch (e) {
    toast(`Move failed: ${e.message}`, "error");
  }
}

async function triggerSync() {
  if (state.syncing) return;
  state.syncing = true;
  $("sync-btn").disabled = true;
  setStatus("Syncing…");
  try {
    const summary = await api("/api/sync", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const failed = (summary.failures || []).length;
    setStatus(`Synced — ${summary.new} new, ${summary.skipped} skipped, ${failed} failed`);
    if (failed) {
      toast(`${failed} message(s) could not be imported. Check disk space, folder access, and Gmail connectivity. Retry Sync to recover incomplete messages.`, "error");
    }
    await loadFolders();
    if (state.view === "board") {
      await renderBoard();
    } else {
      await loadMessages(state.currentFolder);
    }
  } catch (e) {
    setStatus("");
    toast(`Sync failed: ${e.message}`, "error");
  } finally {
    state.syncing = false;
    $("sync-btn").disabled = false;
    setTimeout(() => setStatus(""), 6000);
  }
}

async function createFolder() {
  const name = prompt("Folder name:");
  if (!name) return;
  try {
    await api("/api/folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    });
    toast(`Created folder ${name}`);
    await loadFolders();
  } catch (e) {
    toast(`Couldn't create folder: ${e.message}`, "error");
  }
}

// ---------- compose / reply ----------
function openCompose({ replyTo } = {}) {
  let pending = null;
  try { pending = JSON.parse(sessionStorage.getItem("mailbox.pendingSend")); } catch (_e) {}
  let operationId = pending?.operation_id || crypto.randomUUID();
  const fm = replyTo ? (replyTo.frontmatter || {}) : {};
  const isReply = Boolean(replyTo);
  const subject = isReply
    ? (fm.subject?.startsWith("Re:") ? fm.subject : `Re: ${fm.subject || ""}`)
    : "";
  const to = isReply ? (fm.from || "") : "";
  const inReplyTo = isReply ? (fm["message-id"] || "") : "";
  const refs = isReply
    ? [...(fm.references || []), fm["message-id"]].filter(Boolean).join(" ")
    : "";

  const root = $("modal-root");
  root.innerHTML = `
    <div class="modal-backdrop">
      <div class="modal" role="dialog" aria-label="Compose">
        <div class="modal-header">
          <span>${isReply ? "Reply" : "New message"}</span>
          <button type="button" class="close-x" id="compose-close">×</button>
        </div>
        <div class="modal-body">
          <label>To</label>
          <input type="text" id="compose-to" value="${escapeHtml(to)}" />
          <label>Cc</label>
          <input type="text" id="compose-cc" value="" />
          <label>Subject</label>
          <input type="text" id="compose-subject" value="${escapeHtml(subject)}" />
          <label>Message</label>
          <textarea id="compose-body"></textarea>
          <p id="compose-status" role="status"></p>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn" id="compose-cancel">Cancel</button>
          <button type="button" class="btn btn-primary" id="compose-send">Send</button>
        </div>
      </div>
    </div>
  `;
  const dialog = root.querySelector(".modal");
  const composeElement = (id) => dialog.querySelector("#" + id);
  const close = () => {
    if (root.contains(dialog)) root.innerHTML = "";
  };
  composeElement("compose-close").addEventListener("click", close);
  composeElement("compose-cancel").addEventListener("click", close);
  if (pending) {
    for (const field of ["to", "cc", "subject", "body"]) composeElement("compose-" + field).value = pending[field] || "";
    composeElement("compose-status").textContent = "A previous send needs confirmation. Check its status before creating another message.";
    composeElement("compose-send").textContent = "Check send status";
    for (const field of ["to", "cc", "subject", "body"]) composeElement("compose-" + field).disabled = true;
  }
  composeElement("compose-send").addEventListener("click", async () => {
    const body = pending || {
      operation_id: operationId,
      to: composeElement("compose-to").value.trim(),
      cc: composeElement("compose-cc").value.trim(),
      subject: composeElement("compose-subject").value.trim(),
      body: composeElement("compose-body").value,
      in_reply_to: inReplyTo || null,
      references: refs || null,
    };
    if (!body.to) { toast("To: is required", "error"); return; }
    pending = body;
    sessionStorage.setItem("mailbox.pendingSend", JSON.stringify(pending));
    for (const field of ["to", "cc", "subject", "body"]) composeElement("compose-" + field).disabled = true;
    composeElement("compose-send").disabled = true;
    composeElement("compose-send").textContent = "Sending…";
    try {
      const result = await api("/api/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (result.status === "unknown") {
        composeElement("compose-status").textContent = "Delivery is unknown. Check Gmail Sent before creating another message. This message will not be sent again automatically.";
        composeElement("compose-send").textContent = "Delivery unknown";
        return;
      }
      if (result.status === "sent_unmirrored") {
        composeElement("compose-status").textContent = "Gmail accepted this message, but the local copy could not be saved. Do not resend.";
        composeElement("compose-send").textContent = "Retry local save";
        composeElement("compose-send").disabled = false;
        return;
      }
      if (result.status === "rejected") {
        sessionStorage.removeItem("mailbox.pendingSend");
        pending = null;
        operationId = crypto.randomUUID();
        for (const field of ["to", "cc", "subject", "body"]) composeElement("compose-" + field).disabled = false;
        composeElement("compose-status").textContent = "Gmail rejected this message. Review the recipients and try again.";
        composeElement("compose-send").disabled = false;
        composeElement("compose-send").textContent = "Send";
        return;
      }
      if (result.status !== "sent") throw new Error("Unrecognized delivery status.");
      sessionStorage.removeItem("mailbox.pendingSend");
      toast("Sent.");
      close();
      try {
        await loadFolders();
        if (state.currentFolder === "sent") await loadMessages("sent");
      } catch (_refreshError) {
        // Delivery was confirmed. A stale token/offline refresh cannot undo it.
        toast("Sent, but the mailbox could not refresh. Reload the page; do not resend.", "error");
      }
    } catch (e) {
      if (e.code === "authorization_required") {
        sessionStorage.removeItem("mailbox.pendingSend");
        pending = null;
        operationId = crypto.randomUUID();
        for (const field of ["to", "cc", "subject", "body"]) composeElement("compose-" + field).disabled = false;
        composeElement("compose-status").textContent = e.message;
        composeElement("compose-send").disabled = false;
        composeElement("compose-send").textContent = "Send";
        return;
      }
      composeElement("compose-status").textContent = `Send confirmation unavailable: ${e.message} Check status using the same operation before creating another message.`;
      composeElement("compose-send").disabled = false;
      composeElement("compose-send").textContent = "Check send status";
    }
  });
  // The modal already exists. Deferred focus can steal keystrokes from a user
  // who has selected another field (including moving subject text into To).
  composeElement(isReply ? "compose-body" : "compose-to").focus();
}

// ---------- board (Kanban) view ----------
function setView(v) {
  state.view = v;
  localStorage.setItem("mailbox.view", v);
  document.body.dataset.view = v;
  document.querySelectorAll(".view-toggle button").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === v);
  });
  if (v === "board") {
    renderBoard();
  } else {
    renderFolders();
    if (state.currentFolder) loadMessages(state.currentFolder);
  }
}

async function renderBoard() {
  const board = $("board");
  if (!board) return;
  // Top-level folders only become columns
  const cols = (state.folders || []).filter((f) => (f.depth || 0) === 0);
  board.innerHTML = "";
  if (!cols.length) {
    board.innerHTML = `<div class="empty-list" style="padding:40px;">No folders yet.</div>`;
    return;
  }
  for (const f of cols) {
    const col = document.createElement("div");
    col.className = "column";
    col.dataset.path = f.path;
    col.innerHTML = `
      <div class="column-header">
        <span class="name">${escapeHtml(f.name)}</span>
        <span class="count">${f.count}</span>
      </div>
      <div class="column-body" data-folder="${escapeHtml(f.path)}">
        <div class="empty-col">Loading…</div>
      </div>
    `;
    board.appendChild(col);
    const body = col.querySelector(".column-body");
    body.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      body.classList.add("drop-target");
    });
    body.addEventListener("dragleave", () => body.classList.remove("drop-target"));
    body.addEventListener("drop", async (e) => {
      e.preventDefault();
      body.classList.remove("drop-target");
      const path = e.dataTransfer.getData("text/x-mailbox-path");
      if (path) await moveMessage(path, f.path);
    });
  }
  // Fetch messages for each column in parallel
  await Promise.all(cols.map(async (f) => {
    let messages = [];
    try {
      messages = await api(`/api/messages?folder=${encodeURIComponent(f.path)}`);
    } catch (_e) { messages = []; }
    const body = board.querySelector(`.column-body[data-folder="${cssAttrEscape(f.path)}"]`);
    if (!body) return;
    body.innerHTML = "";
    if (!messages.length) {
      body.innerHTML = `<div class="empty-col">Empty</div>`;
      return;
    }
    for (const m of messages) {
      const card = document.createElement("div");
      card.className = "card" + (m.unread ? " unread" : "");
      card.draggable = true;
      card.dataset.path = m.path;
      card.innerHTML = `
        <div class="from-row"><span class="from">${escapeHtml(displayName(m.from))}</span><span class="date">${escapeHtml(fmtDate(m.date))}</span></div>
        <div class="subject">${m.has_attachments ? "📎 " : ""}${escapeHtml(m.subject)}</div>
        <div class="snippet">${escapeHtml(m.snippet)}</div>
      `;
      card.addEventListener("click", () => openMessageModal(m.path));
      card.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("text/x-mailbox-path", m.path);
        e.dataTransfer.effectAllowed = "move";
        card.classList.add("dragging");
      });
      card.addEventListener("dragend", () => card.classList.remove("dragging"));
      body.appendChild(card);
    }
  }));
}

function cssAttrEscape(s) {
  return String(s).replace(/"/g, '\\"');
}

async function openMessageModal(path) {
  let data;
  try {
    data = await api(`/api/message?path=${encodeURIComponent(path)}`);
  } catch (e) {
    toast(`Couldn't open: ${e.message}`, "error");
    return;
  }
  const fm = data.frontmatter || {};
  const md = data.body || "";
  const bodyHtml = renderMarkdown(md);
  const attachHtml = (data.attachments || []).map((a) => {
    const id = fm.id;
    const url = `/api/attachment/${encodeURIComponent(id)}/${encodeURIComponent(a.filename)}`;
    return `<a class="attachment-item" href="#" data-url="${escapeHtml(url)}" data-filename="${escapeHtml(a.filename)}">📎 ${escapeHtml(a.filename)} <span class="size">${fmtSize(a.size)}</span></a>`;
  }).join("");
  const metaRows = [
    fm.from ? `<div><span class="label">From:</span> <span class="value">${escapeHtml(fm.from)}</span></div>` : "",
    fm.date ? `<div><span class="label">Date:</span> <span class="value">${escapeHtml(fm.date)}</span></div>` : "",
  ].filter(Boolean).join("");
  const isEmail = Boolean(fm.from || fm.id);
  const root = $("modal-root");
  root.innerHTML = `
    <div class="modal-backdrop">
      <div class="modal modal-reader">
        <div class="modal-header">
          <span>${escapeHtml(fm.subject || data.path?.split("/").pop() || "(no subject)")}</span>
          <button type="button" class="close-x" id="reader-close">×</button>
        </div>
        <div class="modal-body">
          ${metaRows ? `<div class="reader-meta">${metaRows}</div>` : ""}
          <div class="reader-body" style="margin-top:14px">${bodyHtml}</div>
          ${attachHtml ? `<div class="attachments" style="border-top:1px solid #e6e8eb; margin-top:14px; padding-top:14px;"><h3>Attachments</h3>${attachHtml}</div>` : ""}
        </div>
        <div class="modal-footer">
          <button type="button" class="btn" id="reader-cancel">Close</button>
          <button type="button" class="btn" id="reader-open-file">Open in editor</button>
          ${isEmail ? `<button type="button" class="btn btn-primary" id="reader-reply">Reply</button>` : ""}
        </div>
      </div>
    </div>
  `;
  const close = () => (root.innerHTML = "");
  $("reader-close").addEventListener("click", close);
  $("reader-cancel").addEventListener("click", close);
  bindAttachments(root);
  $("reader-open-file").addEventListener("click", () => openFileInEditor(data.path));
  if (isEmail) {
    $("reader-reply").addEventListener("click", () => {
      close();
      openCompose({ replyTo: data });
    });
  }
}

// ---------- keyboard shortcuts ----------
document.addEventListener("keydown", (e) => {
  if (document.querySelector(".modal-backdrop")) return;
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
  if (e.key === "j" || e.key === "k") {
    if (!state.messages.length) return;
    const idx = state.currentMessage
      ? state.messages.findIndex((m) => m.path === state.currentMessage.path)
      : -1;
    const next = e.key === "j" ? Math.min(state.messages.length - 1, idx + 1)
                                : Math.max(0, idx - 1);
    if (next !== idx) selectMessage(state.messages[next].path);
  }
});

// ---------- bootstrap ----------
$("sync-btn").addEventListener("click", triggerSync);
$("compose-btn").addEventListener("click", () => openCompose({}));
$("new-folder-btn").addEventListener("click", createFolder);
document.querySelectorAll(".view-toggle button").forEach((b) => {
  b.addEventListener("click", () => setView(b.dataset.view));
});

(async () => {
  await loadFolders();
  if (state.folders.some((f) => f.path === "inbox")) {
    state.currentFolder = "inbox";
  } else if (state.folders.length) {
    state.currentFolder = state.folders[0].path;
  }
  $("messages-header").textContent = state.currentFolder;
  renderFolders();
  document.body.dataset.view = state.view;
  document.querySelectorAll(".view-toggle button").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === state.view);
  });
  if (state.view === "board") {
    await renderBoard();
  } else {
    await loadMessages(state.currentFolder);
  }
})();
