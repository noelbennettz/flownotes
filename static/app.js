/**
 * Main controller: tree, note switching, debounced autosave, Ctrl+K search,
 * M3 dialogs, drawer, theme and language. All requests are same-origin.
 */
import {
  apiFetch,
  apiJson,
  applyTheme,
  applyTone,
  askText,
  confirmAction,
  showMenu,
  showSnackbar,
  storedTheme,
  storedTone,
  toggleExplicitTheme,
  watchSystemTheme,
} from "./ui.js";
import { LOCALES, getLocale, initI18n, localeInfo, setLocale, t } from "./i18n.js";

const SAVE_DELAY = 600;
const SEARCH_DELAY = 150;
const MOBILE_QUERY = "(max-width: 900px)";

const STATUS_KEYS = {
  saved: "status.saved",
  saving: "status.saving",
  dirty: "status.dirty",
  error: "status.error",
};

const ICONS = {
  chevron:
    '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M10 8l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  noteAdd:
    '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M12 6v12M6 12h12" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></svg>',
  rename:
    '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M15.5 4.9a1.9 1.9 0 0 1 2.7 2.7L8.6 17.2 5 18l.8-3.6z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>',
  trash:
    '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M5 7h14M9.5 7V5.2h5V7M6.8 7l.6 12h9.2l.6-12" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

const els = {
  layout: document.getElementById("layout"),
  drawer: document.getElementById("drawer"),
  scrim: document.getElementById("drawer-scrim"),
  tree: document.getElementById("tree"),
  breadcrumb: document.getElementById("breadcrumb"),
  status: document.getElementById("save-status"),
  canvas: document.getElementById("canvas"),
  editorHost: document.getElementById("editor"),
  newNote: document.getElementById("btn-new-note"),
  newFolder: document.getElementById("btn-new-folder"),
  searchTrigger: document.getElementById("btn-search"),
  themeButton: document.getElementById("btn-theme"),
  languageButton: document.getElementById("btn-language"),
  exportButton: document.getElementById("btn-export"),
  toggleDrawer: document.getElementById("btn-toggle-drawer"),
  closeDrawer: document.getElementById("btn-close-drawer"),
  userName: document.getElementById("user-name"),
  userAvatar: document.getElementById("user-avatar"),
  searchDialog: document.getElementById("search-dialog"),
  searchInput: document.getElementById("search-input"),
  searchResults: document.getElementById("search-results"),
  settingsLink: document.getElementById("btn-settings"),
  settingsBack: document.getElementById("btn-settings-back"),
};

const state = {
  username: null,
  tree: [],
  currentPath: null,
  version: null,
  // The text this device last read from (or wrote to) the server. It is sent back as
  // base_content so a save that lost the race is merged instead of overwriting the other side.
  baseMarkdown: null,
  dirty: false,
  saveTimer: null,
  searchTimer: null,
  searchResults: [],
  searchIndex: -1,
  pendingMarkdown: null,
  // Text sent by the hidden-tab keepalive flush; its response is unreadable, so the outcome
  // is confirmed by comparing the stored note when the tab becomes visible again.
  pendingWrite: null,
  expanded: new Set(),
};

let currentStatus = "saved";

initI18n();
applyTheme(storedTheme());
applyTone(storedTone());

function scopedKey(name) {
  return `flownotes:${name}:${state.username || "anon"}`;
}

function readJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (error) {
    return fallback;
  }
}

function setStatus(name, text) {
  currentStatus = name;
  els.status.dataset.state = name;
  els.status.textContent = text || t(STATUS_KEYS[name] || "status.saved");
}

function isMobile() {
  return window.matchMedia(MOBILE_QUERY).matches;
}

function setDrawer(collapsed, { persist = true } = {}) {
  els.layout.classList.toggle("is-collapsed", collapsed);
  if (persist) localStorage.setItem("flownotes:drawer", collapsed ? "collapsed" : "open");
}

function restoreDrawer() {
  const stored = localStorage.getItem("flownotes:drawer");
  if (stored === "open" || stored === "collapsed") {
    setDrawer(stored === "collapsed", { persist: false });
  } else {
    setDrawer(isMobile(), { persist: false });
  }
}

const SETTINGS_PATH = "/settings";

function currentView() {
  return window.location.pathname === SETTINGS_PATH ? "settings" : "notes";
}

function applyDocumentTitle() {
  if (currentView() === "settings") {
    document.title = `${t("settings.title")} · ${t("app.name")}`;
    return;
  }
  document.title = state.username ? `${state.username} · ${t("app.name")}` : t("app.name");
}

/**
 * The editor bundle is ~570 kB and only the notes view needs it, so load it when a note is
 * first opened instead of on every page load (a deep link to /settings never fetches it).
 */
let editor = null;

async function ensureEditor() {
  if (editor) return editor;
  const { createEditor } = await import("./editor.js");
  editor = createEditor({
    element: els.editorHost,
    onChange: (markdown) => {
      state.pendingMarkdown = markdown;
      state.dirty = true;
      setStatus("dirty");
      scheduleSave();
    },
    onUpload: (phase) => {
      if (phase === "start") setStatus("saving", t("status.saving"));
      else if (phase === "done") setStatus(state.dirty ? "dirty" : "saved");
      else setStatus("error", t("toast.imageUploadFailed"));
    },
  });
  editor.setPlaceholder(t("editor.placeholder"));
  return editor;
}

function scheduleSave() {
  window.clearTimeout(state.saveTimer);
  state.saveTimer = window.setTimeout(() => {
    void flushSave();
  }, SAVE_DELAY);
}

// Only one save may be in flight: a second request quoting the same base would race the first
// and turn an ordinary merge into a conflict copy. Later edits wait for it and are merged next.
let saveInFlight = null;

async function flushSave() {
  window.clearTimeout(state.saveTimer);
  state.saveTimer = null;
  if (!state.dirty || !state.currentPath || state.pendingMarkdown === null) {
    state.pendingWrite = null;
    return true;
  }

  if (saveInFlight) {
    const previous = await saveInFlight.catch(() => false);
    if (!previous) return false;
    if (state.currentPath === null) return true;
    return flushSave();
  }

  const path = state.currentPath;
  if (state.pendingWrite?.path === path) {
    // A keepalive flush may already have stored the text it sent; its response was unreadable.
    // Settle it first so this save quotes the right base.
    await confirmPendingWrite(path);
    if (state.currentPath !== path) return false;
    if (!state.dirty) return true;
  }

  const content = state.pendingMarkdown;
  if (content === null) return true;
  setStatus("saving");
  // base_content lets the server merge when another device saved between our read and this
  // write; without it a mismatch is a plain 409.
  const body = { path, content, base_version: state.version };
  if (typeof state.baseMarkdown === "string") body.base_content = state.baseMarkdown;

  saveInFlight = (async () => {
    try {
      const data = await apiJson("/api/note", { method: "PUT", body });
      applySaveResult(path, content, data);
      return true;
    } catch (error) {
      if (state.currentPath !== path) return false;
      if (error.status === 404) {
        // Renamed or deleted on another device: drop the stale entry from the tree.
        void refreshTree().catch(() => {});
      }
      setStatus("error", error.message || t("status.error"));
      return false;
    }
  })();
  try {
    return await saveInFlight;
  } finally {
    saveInFlight = null;
  }
}

/* ---------- Concurrent edits ---------- */

/**
 * Apply the server's answer to a save. Three shapes matter: a note that vanished before the
 * write landed (the text was kept as a new note), a merge that could not place both edits
 * (both texts survive, this device's in a “(conflict …)” copy), and a clean merge whose text
 * has to replace what the editor is showing.
 */
function applySaveResult(path, sent, data) {
  const copy = data.conflict_copy;
  if (data.missing || copy) {
    // Follow this device's text to the note that now holds it, so the editor and the file stay
    // in sync; the original keeps whatever the other device saved.
    state.currentPath = copy || path;
    state.version = copy ? (data.conflict_copy_version ?? null) : (data.version ?? null);
    state.baseMarkdown = sent;
    // A keepalive write for the original path must not be confirmed against this copy later.
    state.pendingWrite = null;
    localStorage.setItem(scopedKey("last-note"), state.currentPath);
    expandAncestors(state.currentPath);
    renderBreadcrumb(state.currentPath);
    void refreshTree().catch(() => {});
    if (state.pendingMarkdown !== sent) {
      // Keystrokes arrived while saving: keep them and write them into the copy next tick.
      state.dirty = true;
      setStatus("dirty");
      scheduleSave();
    } else {
      state.pendingMarkdown = sent;
      state.dirty = false;
      setStatus("saved");
    }
    const message = data.missing
      ? t("toast.savedAsCopy", { path: copy })
      : t("toast.keptBoth", { path: copy });
    showSnackbar(message, {
      // A renamed or deleted original has nothing left to open; the copy is what matters.
      actionLabel: data.missing ? undefined : t("toast.openOriginal"),
      onAction: data.missing ? undefined : () => void openNote(path),
      timeout: 8000,
    });
    return;
  }

  const mergedText = typeof data.content === "string" ? data.content : sent;
  if (state.pendingMarkdown !== sent) {
    // Keystrokes arrived while saving. Quote exactly the text this save carried, so the next
    // tick merges those keystrokes on top of whatever the server now holds instead of writing
    // this device's older text over the merge.
    state.baseMarkdown = sent;
    state.version = data.sent_version ?? state.version;
    setStatus("dirty");
    scheduleSave();
    return;
  }
  state.version = data.version ?? state.version;
  state.baseMarkdown = mergedText;
  state.pendingMarkdown = mergedText;
  state.dirty = false;
  setStatus("saved");
  if (mergedText !== sent && editor) {
    editor.setMarkdown(mergedText, { restoreSelection: true });
  }
  if (data.merged) showSnackbar(t("toast.merged"));
}

/** Coming back to the tab: pick up edits saved elsewhere while this note was idle. */
let lastMetaCheck = 0;

/**
 * The response of the keepalive PUT sent while the tab was hidden cannot be read, so its
 * outcome is unknown. Whatever the file now holds becomes this device's merge base; if the
 * stored text is exactly what was sent, the save is simply done. A keepalive write that the
 * server merged with another device's edits arrives here as different text, and showing it
 * keeps the editor from re-saving a stale version over the merge.
 */
async function confirmPendingWrite(path) {
  const pending = state.pendingWrite;
  if (!pending || pending.path !== path) return false;
  state.pendingWrite = null;
  try {
    const data = await apiJson(`/api/note?path=${encodeURIComponent(path)}`);
    if (state.currentPath !== path) return false;
    const stored = data.content ?? "";
    state.version = data.version ?? null;
    state.baseMarkdown = stored;
    if (state.pendingMarkdown !== pending.content) {
      // Newer keystrokes are waiting; they will merge against the stored text on the next tick.
      return false;
    }
    state.pendingMarkdown = stored;
    state.dirty = false;
    setStatus("saved");
    if (stored !== pending.content) {
      editor?.setMarkdown(stored, { restoreSelection: true });
      showSnackbar(t("toast.merged"));
    }
    return true;
  } catch (error) {
    return false;
  }
}

async function syncOpenNote() {
  if (document.visibilityState !== "visible") return;
  const path = state.currentPath;
  if (!path) return;

  // A pending keepalive write is the likely reason the version looks stale; settle it (or
  // save newer text on top) before treating any difference as an edit from another device.
  if (state.pendingWrite) {
    if (state.pendingWrite.path !== path) {
      state.pendingWrite = null;
    } else {
      await flushSave();
      if (state.currentPath !== path || state.dirty) return;
    }
  }

  if (state.dirty) return;
  if (Date.now() - lastMetaCheck < 3000) return;
  lastMetaCheck = Date.now();
  try {
    const meta = await apiJson(`/api/note/meta?path=${encodeURIComponent(path)}`);
    if (state.currentPath !== path || state.dirty) return;
    if (!meta.version || meta.version === state.version) return;
    const data = await apiJson(`/api/note?path=${encodeURIComponent(path)}`);
    if (state.currentPath !== path || state.dirty) return;
    const view = await ensureEditor();
    state.version = data.version ?? null;
    state.baseMarkdown = data.content ?? "";
    state.pendingMarkdown = null;
    view.setMarkdown(data.content || "", { restoreSelection: true });
    setStatus("saved");
    showSnackbar(t("toast.reloadedFromServer"));
  } catch (error) {
    if (error.status !== 404) return;
    // Deleted or renamed elsewhere: clear the stale note instead of silently saving into nothing.
    window.clearTimeout(state.saveTimer);
    state.currentPath = null;
    state.version = null;
    state.dirty = false;
    state.pendingMarkdown = null;
    resetEditorView();
    localStorage.removeItem(scopedKey("last-note"));
    showSnackbar(t("toast.noteGone"));
    await refreshTree().catch(() => {});
  }
}

/** Send pending edits when the tab is closed or hidden (keepalive fetch). */
function flushOnLeave() {
  if (!state.dirty || !state.currentPath || state.pendingMarkdown === null) return;
  const path = state.currentPath;
  const content = state.pendingMarkdown;
  if (state.pendingWrite?.path === path && state.pendingWrite.content === content) return;
  // The response cannot be read while the page is going away, so remember what was sent and
  // stay dirty: syncOpenNote() confirms the write when the tab is shown again. Marking the
  // note clean here would also lose the text if the keepalive request never arrived.
  state.pendingWrite = { path, content };
  window.clearTimeout(state.saveTimer);
  state.saveTimer = null;
  try {
    fetch("/api/note", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, content, base_version: state.version }),
      keepalive: true,
    }).catch(() => {});
  } catch (error) {
    /* Nothing else we can do while unloading. */
  }
}

function displayName(name) {
  return name.replace(/\.md$/i, "");
}

function renderNode(node, depth) {
  if (node.type === "folder") {
    const wrapper = document.createElement("div");
    wrapper.className = "tree-node";
    wrapper.dataset.path = node.path;
    wrapper.dataset.type = "folder";
    if (state.expanded.has(node.path)) wrapper.classList.add("is-open");

    const row = document.createElement("div");
    row.className = "tree-row tree-row--folder";
    row.style.paddingInlineStart = `${8 + depth * 14}px`;
    row.dataset.path = node.path;
    row.dataset.type = "folder";
    row.tabIndex = 0;
    row.setAttribute("role", "treeitem");
    row.setAttribute("aria-expanded", String(state.expanded.has(node.path)));
    row.draggable = true;

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "tree-toggle";
    toggle.dataset.action = "toggle";
    toggle.setAttribute("aria-label", node.name);
    toggle.innerHTML = ICONS.chevron;

    const label = document.createElement("span");
    label.className = "tree-label";
    label.textContent = node.name;

    const actions = document.createElement("span");
    actions.className = "tree-actions";
    actions.innerHTML =
      `<button type="button" class="tree-action" data-action="new-note" title="${t("sidebar.newNote")}" aria-label="${t("sidebar.newNote")}">${ICONS.noteAdd}</button>` +
      `<button type="button" class="tree-action" data-action="rename" title="${t("common.rename")}" aria-label="${t("common.rename")}">${ICONS.rename}</button>` +
      `<button type="button" class="tree-action" data-action="delete" title="${t("common.delete")}" aria-label="${t("common.delete")}">${ICONS.trash}</button>`;

    row.append(toggle, label, actions);

    const children = document.createElement("div");
    children.className = "tree-children";
    (node.children || []).forEach((child) => children.append(renderNode(child, depth + 1)));

    wrapper.append(row, children);
    return wrapper;
  }

  const row = document.createElement("div");
  row.className = "tree-row tree-row--file";
  row.style.paddingInlineStart = `${8 + depth * 14 + 28}px`;
  row.dataset.path = node.path;
  row.dataset.type = "file";
  row.tabIndex = 0;
  row.setAttribute("role", "treeitem");
  row.draggable = true;
  if (node.path === state.currentPath) row.classList.add("is-active");

  const label = document.createElement("span");
  label.className = "tree-label";
  label.textContent = displayName(node.name);

  const actions = document.createElement("span");
  actions.className = "tree-actions";
  actions.innerHTML =
    `<button type="button" class="tree-action" data-action="rename" title="${t("common.rename")}" aria-label="${t("common.rename")}">${ICONS.rename}</button>` +
    `<button type="button" class="tree-action" data-action="delete" title="${t("common.delete")}" aria-label="${t("common.delete")}">${ICONS.trash}</button>`;

  row.append(label, actions);
  return row;
}

function renderTree() {
  els.tree.textContent = "";
  if (state.tree.length === 0) {
    const empty = document.createElement("p");
    empty.className = "tree__empty";
    empty.textContent = t("canvas.emptyTitle");
    els.tree.append(empty);
    return;
  }
  state.tree.forEach((node) => els.tree.append(renderNode(node, 0)));
}

function highlightCurrent() {
  els.tree.querySelectorAll(".tree-row").forEach((row) => {
    const active = row.dataset.type === "file" && row.dataset.path === state.currentPath;
    row.classList.toggle("is-active", active);
  });
}

async function refreshTree() {
  const data = await apiJson("/api/tree");
  state.tree = Array.isArray(data.tree) ? data.tree : [];
  renderTree();
  highlightCurrent();
}

function persistExpanded() {
  localStorage.setItem(scopedKey("expanded"), JSON.stringify([...state.expanded]));
}

function expandAncestors(path) {
  const parts = path.split("/");
  parts.pop();
  let current = "";
  parts.forEach((part) => {
    current = current ? `${current}/${part}` : part;
    state.expanded.add(current);
  });
  persistExpanded();
}

function renderBreadcrumb(path) {
  els.breadcrumb.textContent = "";
  if (!path) return;
  const parts = displayName(path).split("/");
  parts.forEach((part, index) => {
    const item = document.createElement("span");
    item.className = "breadcrumb__item";
    item.textContent = part;
    els.breadcrumb.append(item);
    if (index < parts.length - 1) {
      const separator = document.createElement("span");
      separator.className = "breadcrumb__sep";
      separator.textContent = "/";
      els.breadcrumb.append(separator);
    }
  });
}

function resetEditorView() {
  state.currentPath = null;
  state.version = null;
  state.baseMarkdown = null;
  state.dirty = false;
  state.pendingMarkdown = null;
  state.pendingWrite = null;
  // The editor only exists once a note has been opened; never load it just to clear it.
  if (editor) editor.setMarkdown("");
  els.canvas.classList.remove("has-note");
  renderBreadcrumb(null);
  setStatus("saved");
  highlightCurrent();
}

async function openNote(path) {
  if (path === state.currentPath) {
    // Tapping the already-open note should still close the drawer on mobile.
    if (isMobile()) setDrawer(true);
    // It is also a cheap way to pick up an edit made on another device.
    await syncOpenNote();
    return;
  }
  if (!(await flushSave())) {
    showSnackbar(t("toast.saveBlocked"));
    return;
  }
  try {
    const data = await apiJson(`/api/note?path=${encodeURIComponent(path)}`);
    const view = await ensureEditor();
    state.currentPath = path;
    state.version = data.version ?? null;
    state.baseMarkdown = data.content ?? "";
    state.dirty = false;
    state.pendingMarkdown = null;
    state.pendingWrite = null;
    view.setMarkdown(data.content || "");
    els.canvas.classList.add("has-note");
    expandAncestors(path);
    renderBreadcrumb(path);
    renderTree();
    highlightCurrent();
    setStatus("saved");
    localStorage.setItem(scopedKey("last-note"), path);
    if (isMobile()) setDrawer(true);
    view.focus();
  } catch (error) {
    // A stale "last note" (renamed or deleted elsewhere) must not stick around.
    if (localStorage.getItem(scopedKey("last-note")) === path) {
      localStorage.removeItem(scopedKey("last-note"));
    }
    showSnackbar(error.message || t("toast.readFailed"));
  }
}

async function createNote(parentPath = "") {
  const value = await askText({
    title: t("dialog.newNote.title"),
    label: t("dialog.newNote.label"),
    value: parentPath ? `${parentPath}/` : "",
    helper: t("dialog.newNote.helper"),
    confirmLabel: t("common.create"),
  });
  if (!value) return;
  try {
    if (!(await flushSave())) {
      showSnackbar(t("toast.saveBlocked"));
      return;
    }
    const data = await apiJson("/api/note", { method: "POST", body: { path: value, content: "" } });
    expandAncestors(data.path);
    await refreshTree();
    await openNote(data.path);
  } catch (error) {
    showSnackbar(error.message);
  }
}

async function createFolder(parentPath = "") {
  const value = await askText({
    title: t("dialog.newFolder.title"),
    label: t("dialog.newFolder.label"),
    value: parentPath ? `${parentPath}/` : "",
    helper: t("dialog.newFolder.helper"),
    confirmLabel: t("common.create"),
  });
  if (!value) return;
  try {
    const data = await apiJson("/api/folder", { method: "POST", body: { path: value } });
    state.expanded.add(data.path);
    persistExpanded();
    await refreshTree();
  } catch (error) {
    showSnackbar(error.message);
  }
}

function rekeyState(oldPath, newPath, isFolder) {
  if (state.currentPath === oldPath) {
    state.currentPath = newPath;
  } else if (state.currentPath && state.currentPath.startsWith(`${oldPath}/`)) {
    state.currentPath = newPath + state.currentPath.slice(oldPath.length);
  }
  if (isFolder) {
    const moved = [...state.expanded].map((path) => {
      if (path === oldPath) return newPath;
      if (path.startsWith(`${oldPath}/`)) return newPath + path.slice(oldPath.length);
      return path;
    });
    state.expanded = new Set(moved);
    persistExpanded();
  }
  renderBreadcrumb(state.currentPath);
  if (state.currentPath) localStorage.setItem(scopedKey("last-note"), state.currentPath);
}

/** Flatten folders into choices for the rename dialog (skips `exclude` and its children). */
function collectFolders(nodes, exclude = null, depth = 0, out = []) {
  nodes.forEach((node) => {
    if (node.type !== "folder") return;
    if (exclude && (node.path === exclude || node.path.startsWith(`${exclude}/`))) return;
    out.push({ path: node.path, label: `${"  ".repeat(depth)}${node.name}` });
    collectFolders(node.children || [], exclude, depth + 1, out);
  });
  return out;
}

/** Rename dialog: a text field for the name and a select for the target folder. */
function askRename({ title, name, folder, folders }) {
  const scrim = document.getElementById("rename-dialog");
  const titleEl = scrim.querySelector("#rename-title");
  const bodyEl = scrim.querySelector("#rename-body");
  const nameInput = scrim.querySelector("#rename-name");
  const folderSelect = scrim.querySelector("#rename-folder");
  const confirm = scrim.querySelector("#rename-confirm");
  const cancel = scrim.querySelector("#rename-cancel");

  titleEl.textContent = title;
  bodyEl.textContent = t("dialog.rename.body");
  scrim.querySelector("#rename-name-label").textContent = t("dialog.rename.name");
  scrim.querySelector("#rename-folder-label").textContent = t("dialog.rename.folder");
  confirm.textContent = t("common.rename");
  cancel.textContent = t("common.cancel");
  nameInput.value = name;

  folderSelect.textContent = "";
  folderSelect.append(new Option(t("dialog.rename.root"), ""));
  folders.forEach((item) => folderSelect.append(new Option(item.label, item.path)));
  folderSelect.value = folder || "";

  return new Promise((resolve) => {
    const controller = new AbortController();
    const { signal } = controller;
    const close = (result) => {
      controller.abort();
      scrim.hidden = true;
      resolve(result);
    };
    const submit = () => {
      const trimmed = nameInput.value.trim();
      if (!trimmed) {
        nameInput.focus();
        return;
      }
      close({ name: trimmed, folder: folderSelect.value });
    };

    confirm.addEventListener("click", submit, { signal });
    cancel.addEventListener("click", () => close(null), { signal });
    scrim.addEventListener(
      "mousedown",
      (event) => {
        if (event.target === scrim) close(null);
      },
      { signal },
    );
    nameInput.addEventListener(
      "keydown",
      (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          submit();
        }
      },
      { signal },
    );
    document.addEventListener(
      "keydown",
      (event) => {
        if (event.key === "Escape") close(null);
      },
      { signal },
    );

    scrim.hidden = false;
    nameInput.focus();
    nameInput.select();
  });
}

async function renameEntry(row) {
  const current = row.dataset.path;
  const isFolder = row.dataset.type === "folder";
  const segments = (isFolder ? current : displayName(current)).split("/");
  const name = segments.pop();
  const parent = segments.join("/");
  const folders = collectFolders(state.tree, isFolder ? current : null);

  const result = await askRename({
    title: isFolder ? t("dialog.renameFolder.title") : t("dialog.renameNote.title"),
    name,
    folder: parent,
    folders,
  });
  if (!result) return;

  const targetName = isFolder
    ? result.name
    : result.name.toLowerCase().endsWith(".md")
      ? result.name
      : `${result.name}.md`;
  const next = result.folder ? `${result.folder}/${targetName}` : targetName;
  if (next === current) return;

  try {
    if (!(await flushSave())) {
      showSnackbar(t("toast.saveBlocked"));
      return;
    }
    const data = await apiJson("/api/rename", {
      method: "POST",
      body: { old_path: current, new_path: next },
    });
    rekeyState(current, data.new_path, isFolder);
    if (result.folder) {
      state.expanded.add(result.folder);
      persistExpanded();
    }
    await refreshTree();
  } catch (error) {
    showSnackbar(error.message);
  }
}

/** Move a note or folder into targetFolder ("" means the root); used by drag and drop. */
async function movePath(path, type, targetFolder) {
  const name = path.split("/").pop();
  const newPath = targetFolder ? `${targetFolder}/${name}` : name;
  if (newPath === path) return;
  if (type === "folder" && newPath.startsWith(`${path}/`)) {
    showSnackbar(t("toast.invalidMove"));
    return;
  }
  try {
    if (!(await flushSave())) {
      showSnackbar(t("toast.saveBlocked"));
      return;
    }
    const data = await apiJson("/api/rename", {
      method: "POST",
      body: { old_path: path, new_path: newPath },
    });
    rekeyState(path, data.new_path, type === "folder");
    if (targetFolder) {
      state.expanded.add(targetFolder);
      persistExpanded();
    }
    await refreshTree();
    showSnackbar(t("toast.moved"));
  } catch (error) {
    showSnackbar(error.message);
  }
}

async function deleteEntry(row) {
  const path = row.dataset.path;
  const isFolder = row.dataset.type === "folder";
  const ok = await confirmAction({
    title: isFolder ? t("dialog.deleteFolder.title") : t("dialog.deleteNote.title"),
    body: isFolder ? t("dialog.deleteFolder.body", { path }) : t("dialog.deleteNote.body", { path }),
    confirmLabel: t("common.delete"),
    danger: true,
  });
  if (!ok) return;
  try {
    if (!(await flushSave())) {
      showSnackbar(t("toast.saveBlocked"));
      return;
    }
    await apiFetch(`/api/note?path=${encodeURIComponent(path)}`, { method: "DELETE" });
    const removingCurrent =
      state.currentPath === path ||
      (isFolder && state.currentPath && state.currentPath.startsWith(`${path}/`));
    if (removingCurrent) {
      window.clearTimeout(state.saveTimer);
      resetEditorView();
      localStorage.removeItem(scopedKey("last-note"));
    }
    state.expanded.delete(path);
    persistExpanded();
    await refreshTree();
    showSnackbar(t("toast.deleted"));
  } catch (error) {
    showSnackbar(error.message);
  }
}

function toggleFolder(path) {
  if (state.expanded.has(path)) state.expanded.delete(path);
  else state.expanded.add(path);
  persistExpanded();
  renderTree();
}

/* ---------- Ctrl+K search ---------- */

function renderSearchHint(text) {
  els.searchResults.textContent = "";
  const hint = document.createElement("p");
  hint.className = "dialog__hint";
  hint.textContent = text;
  els.searchResults.append(hint);
}

function renderSearchResults(query) {
  els.searchResults.textContent = "";
  if (state.searchResults.length === 0) {
    renderSearchHint(t("search.noResults", { query }));
    return;
  }
  state.searchResults.forEach((hit, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `search-hit${index === state.searchIndex ? " is-active" : ""}`;
    button.dataset.index = String(index);

    const title = document.createElement("span");
    title.className = "search-hit__title";
    title.textContent = hit.title || displayName(hit.path);

    const path = document.createElement("span");
    path.className = "search-hit__path";
    path.textContent = hit.path;

    const snippet = document.createElement("span");
    snippet.className = "search-hit__snippet";
    // The server escapes the snippet and only keeps <mark> tags.
    snippet.innerHTML = hit.snippet || "";

    button.append(title, path, snippet);
    els.searchResults.append(button);
  });
}

async function runSearch(query) {
  if (!query.trim()) {
    state.searchResults = [];
    state.searchIndex = -1;
    renderSearchHint(t("search.hint"));
    return;
  }
  try {
    const data = await apiJson(`/api/search?q=${encodeURIComponent(query)}`);
    state.searchResults = Array.isArray(data.results) ? data.results : [];
    state.searchIndex = state.searchResults.length > 0 ? 0 : -1;
    renderSearchResults(query);
  } catch (error) {
    renderSearchHint(error.message);
  }
}

async function openSearchResult(index) {
  const hit = state.searchResults[index];
  if (!hit) return;
  closeSearch();
  await openNote(hit.path);
}

function moveSearchSelection(delta) {
  if (state.searchResults.length === 0) return;
  const length = state.searchResults.length;
  state.searchIndex = (state.searchIndex + delta + length) % length;
  const items = els.searchResults.querySelectorAll(".search-hit");
  items.forEach((item, itemIndex) => item.classList.toggle("is-active", itemIndex === state.searchIndex));
  items[state.searchIndex]?.scrollIntoView({ block: "nearest" });
}

function openSearch() {
  els.searchDialog.hidden = false;
  els.searchInput.value = "";
  state.searchResults = [];
  state.searchIndex = -1;
  renderSearchHint(t("search.hint"));
  els.searchInput.focus();
}

function closeSearch() {
  els.searchDialog.hidden = true;
}

/* ---------- Language and theme ---------- */

async function pickLanguage() {
  const choice = await showMenu(
    els.languageButton,
    LOCALES.map((locale) => ({
      value: locale.code,
      label: locale.name,
      selected: locale.code === getLocale(),
    })),
  );
  if (!choice) return;
  setLocale(choice);
}

function relocalize() {
  applyDocumentTitle();
  setStatus(currentStatus);
  renderTree();
  highlightCurrent();
  els.themeButton.title = t("sidebar.theme");
  els.themeButton.setAttribute("aria-label", t("sidebar.theme"));
  els.languageButton.title = `${t("sidebar.language")} · ${localeInfo().name}`;
  els.languageButton.setAttribute("aria-label", t("sidebar.language"));
  if (editor) editor.setPlaceholder(t("editor.placeholder"));
  if (!els.searchDialog.hidden) {
    if (els.searchInput.value.trim()) renderSearchResults(els.searchInput.value);
    else renderSearchHint(t("search.hint"));
  }
}

/* ---------- Events ---------- */

els.newNote.addEventListener("click", () => void createNote());
els.newFolder.addEventListener("click", () => void createFolder());
els.searchTrigger.addEventListener("click", openSearch);
els.exportButton.addEventListener("click", () => {
  window.location.href = "/api/export";
});
els.languageButton.addEventListener("click", () => void pickLanguage());
els.themeButton.addEventListener("click", () => {
  toggleExplicitTheme();
});

els.toggleDrawer.addEventListener("click", () => {
  setDrawer(!els.layout.classList.contains("is-collapsed"));
});
els.closeDrawer.addEventListener("click", () => setDrawer(true));
els.scrim.addEventListener("click", () => setDrawer(true));

els.tree.addEventListener("click", (event) => {
  const row = event.target.closest(".tree-row");
  if (!row) return;
  const actionButton = event.target.closest("[data-action]");
  if (actionButton) {
    event.stopPropagation();
    const path = row.dataset.path;
    switch (actionButton.dataset.action) {
      case "toggle":
        toggleFolder(path);
        break;
      case "new-note":
        void createNote(path);
        break;
      case "rename":
        void renameEntry(row);
        break;
      case "delete":
        void deleteEntry(row);
        break;
      default:
        break;
    }
    return;
  }
  if (row.dataset.type === "folder") toggleFolder(row.dataset.path);
  else void openNote(row.dataset.path);
});

/* Drag and drop: drop a row on a folder (or on the empty tree area for the root). */
let dragState = null;

function clearDropStyles() {
  els.tree.classList.remove("is-drop-target");
  els.tree
    .querySelectorAll(".is-drop-target")
    .forEach((element) => element.classList.remove("is-drop-target"));
}

function canDropInto(folderPath) {
  if (!dragState) return false;
  return folderPath !== dragState.path && !folderPath.startsWith(`${dragState.path}/`);
}

els.tree.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const row = event.target.closest(".tree-row");
  if (!row) return;
  event.preventDefault();
  if (row.dataset.type === "folder") toggleFolder(row.dataset.path);
  else void openNote(row.dataset.path);
});

els.tree.addEventListener("dragstart", (event) => {
  const row = event.target.closest(".tree-row");
  if (!row || event.target.closest(".tree-action")) {
    event.preventDefault();
    return;
  }
  dragState = { path: row.dataset.path, type: row.dataset.type };
  row.classList.add("is-dragging");
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", row.dataset.path);
  }
});

els.tree.addEventListener("dragend", () => {
  dragState = null;
  els.tree.querySelectorAll(".is-dragging").forEach((element) => element.classList.remove("is-dragging"));
  clearDropStyles();
});

els.tree.addEventListener("dragover", (event) => {
  if (!dragState) return;
  const row = event.target.closest(".tree-row");
  clearDropStyles();
  if (!row) {
    event.preventDefault();
    els.tree.classList.add("is-drop-target");
    return;
  }
  if (row.dataset.type !== "folder" || !canDropInto(row.dataset.path)) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
  row.classList.add("is-drop-target");
});

els.tree.addEventListener("drop", async (event) => {
  if (!dragState) return;
  const drag = dragState;
  const row = event.target.closest(".tree-row");
  dragState = null;
  els.tree.querySelectorAll(".is-dragging").forEach((element) => element.classList.remove("is-dragging"));
  clearDropStyles();
  event.preventDefault();
  if (!row) {
    await movePath(drag.path, drag.type, "");
    return;
  }
  if (row.dataset.type !== "folder") return;
  if (row.dataset.path === drag.path || row.dataset.path.startsWith(`${drag.path}/`)) {
    showSnackbar(t("toast.invalidMove"));
    return;
  }
  await movePath(drag.path, drag.type, row.dataset.path);
});

els.searchResults.addEventListener("click", (event) => {
  const hit = event.target.closest(".search-hit");
  if (!hit) return;
  void openSearchResult(Number(hit.dataset.index));
});

els.searchDialog.addEventListener("mousedown", (event) => {
  if (event.target === els.searchDialog) closeSearch();
});

els.searchInput.addEventListener("input", () => {
  const query = els.searchInput.value;
  window.clearTimeout(state.searchTimer);
  state.searchTimer = window.setTimeout(() => {
    void runSearch(query);
  }, SEARCH_DELAY);
});

els.searchInput.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeSearch();
    return;
  }
  if (event.key === "ArrowDown") {
    event.preventDefault();
    moveSearchSelection(1);
    return;
  }
  if (event.key === "ArrowUp") {
    event.preventDefault();
    moveSearchSelection(-1);
    return;
  }
  if (event.key === "Enter") {
    event.preventDefault();
    if (state.searchIndex >= 0) void openSearchResult(state.searchIndex);
    else void runSearch(els.searchInput.value);
  }
});

document.addEventListener("keydown", (event) => {
  const isSearchShortcut = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k";
  if (isSearchShortcut) {
    event.preventDefault();
    if (els.searchDialog.hidden) openSearch();
    else closeSearch();
    return;
  }
  if (event.key === "Escape" && !els.searchDialog.hidden) closeSearch();
});

document.addEventListener("localechange", relocalize);

window.addEventListener("pagehide", flushOnLeave);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") flushOnLeave();
  else void syncOpenNote();
});
window.addEventListener("focus", () => void syncOpenNote());

window.matchMedia(MOBILE_QUERY).addEventListener("change", () => {
  if (!isMobile() && els.layout.classList.contains("is-collapsed")) setDrawer(false, { persist: false });
});

/* ---------- Views and history ---------- */

let settingsModule = null;
let notesStarted = null;

/** Show the notes or the settings view for the current URL, without reloading the document. */
async function showView() {
  const view = currentView();
  document.documentElement.dataset.view = view;
  applyDocumentTitle();
  if (view === "settings") {
    settingsModule ??= import("./settings.js");
    const module = await settingsModule;
    await module.initSettings();
    return;
  }
  void initNotes();
}

function openSettings() {
  if (currentView() === "settings") return;
  if (isMobile()) setDrawer(true, { persist: false });
  history.pushState({ view: "settings" }, "", SETTINGS_PATH);
  void showView();
}

function closeSettings() {
  if (history.state && history.state.view === "settings") {
    history.back();
    return;
  }
  // Opened from a bookmark, a reload or a new tab: there is no in-app entry to go back to.
  history.replaceState(null, "", "/");
  void showView();
}

els.settingsLink.addEventListener("click", (event) => {
  // Middle-click and modifier-clicks keep the plain link behaviour (new tab, new window).
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  openSettings();
});

els.settingsBack.addEventListener("click", closeSettings);
window.addEventListener("popstate", () => void showView());

/* ---------- Startup ---------- */

function findFirstNote(nodes) {
  for (const node of nodes) {
    if (node.type === "file") return node.path;
    const nested = findFirstNote(node.children || []);
    if (nested) return nested;
  }
  return null;
}

/** Load the tree and the last note once; later visits to the notes view reuse it. */
function initNotes() {
  notesStarted ??= startNotes();
  return notesStarted;
}

async function startNotes() {
  try {
    const me = await apiJson("/api/me");
    state.username = me.username;
    els.userName.textContent = me.username;
    els.userAvatar.textContent = me.username.slice(0, 1).toUpperCase();
    applyDocumentTitle();
  } catch (error) {
    if (error.message) showSnackbar(error.message);
    return;
  }

  state.expanded = new Set(readJson(scopedKey("expanded"), []));

  try {
    await refreshTree();
  } catch (error) {
    els.tree.textContent = error.message;
    return;
  }

  const last = localStorage.getItem(scopedKey("last-note"));
  if (last) await openNote(last);
  if (!state.currentPath) {
    const first = findFirstNote(state.tree);
    if (first) await openNote(first);
  }
}

async function init() {
  watchSystemTheme();
  restoreDrawer();
  relocalize();
  await showView();
}

void init();
