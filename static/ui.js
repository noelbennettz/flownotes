/**
 * Shared UI helpers: theme, fetch wrapper, snackbar, M3 dialogs and menus.
 * Everything is same-origin; no external resources are used.
 */

import { t } from "./i18n.js";

const THEME_STORAGE = "flownotes:theme";
const TONE_STORAGE = "flownotes:tone";
const CONFLICT_STORAGE = "flownotes:conflict";

/** Accent presets; "neutral" is the default black/white M3 scheme. */
export const TONES = ["neutral", "blue", "pink", "red", "yellow", "cyan"];

export function storedTone() {
  const value = localStorage.getItem(TONE_STORAGE);
  return TONES.includes(value) ? value : "neutral";
}

export function applyTone(tone = storedTone()) {
  const value = TONES.includes(tone) ? tone : "neutral";
  document.documentElement.dataset.tone = value;
  localStorage.setItem(TONE_STORAGE, value);
  return value;
}

/**
 * What to do when another device saved the same note first: "mine" saves this device's text
 * over it without asking (the default), "ask" opens the conflict dialog.
 */
export function storedConflictPolicy() {
  return localStorage.getItem(CONFLICT_STORAGE) === "ask" ? "ask" : "mine";
}

export function applyConflictPolicy(policy) {
  const value = policy === "ask" ? "ask" : "mine";
  localStorage.setItem(CONFLICT_STORAGE, value);
  return value;
}

export function storedTheme() {
  const value = localStorage.getItem(THEME_STORAGE);
  return value === "light" || value === "dark" || value === "system" ? value : "system";
}

function systemTheme() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(choice = storedTheme()) {
  const resolved = choice === "system" ? systemTheme() : choice;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.style.colorScheme = resolved;
  localStorage.setItem(THEME_STORAGE, choice);
  return resolved;
}

export function toggleExplicitTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next);
  return next;
}

/** Keep “follow system” in sync when the OS theme changes. */
export function watchSystemTheme(onChange) {
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  const handler = () => {
    if (storedTheme() !== "system") return;
    applyTheme("system");
    onChange?.();
  };
  query.addEventListener("change", handler);
  return () => query.removeEventListener("change", handler);
}

export async function apiFetch(path, { method = "GET", body, headers } = {}) {
  const init = { method, headers: { ...(headers || {}) } };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers["Content-Type"] = "application/json";
  }

  const response = await fetch(path, init);
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error(t("error.unauthorized"));
  }
  if (!response.ok) {
    // Prefer a localized message for well-known statuses; fall back to the server text.
    const byStatus = {
      403: "error.forbidden",
      404: "error.notFound",
      409: "error.conflict",
      429: "error.rateLimited",
    };
    let message = byStatus[response.status] ? t(byStatus[response.status]) : t("error.server");
    let detail;
    try {
      const data = await response.json();
      detail = data ? data.detail : undefined;
      if (detail && !byStatus[response.status]) {
        message = typeof detail === "string" ? detail : String(detail);
      }
    } catch (error) {
      /* keep the status-based message */
    }
    // Callers need the status: a 409 on save means another device changed the note.
    const failure = new Error(message);
    failure.status = response.status;
    failure.detail = detail;
    throw failure;
  }
  return response;
}

export async function apiJson(path, options) {
  return (await apiFetch(path, options)).json();
}

let snackbarTimer = null;

function hideSnackbar() {
  const element = document.getElementById("snackbar");
  if (!element) return;
  element.hidden = true;
  window.clearTimeout(snackbarTimer);
}

export function showSnackbar(message, { actionLabel, onAction, timeout = 4500 } = {}) {
  const element = document.getElementById("snackbar");
  if (!element) return;
  element.textContent = "";

  const text = document.createElement("span");
  text.className = "snackbar__text";
  text.textContent = message;
  element.append(text);

  if (actionLabel) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "snackbar__action";
    button.textContent = actionLabel;
    button.addEventListener("click", () => {
      hideSnackbar();
      onAction?.();
    });
    element.append(button);
  }

  element.hidden = false;
  window.clearTimeout(snackbarTimer);
  snackbarTimer = window.setTimeout(hideSnackbar, timeout);
}

export function askText({
  title = "",
  label = "",
  body = "",
  value = "",
  helper = "",
  confirmLabel = "",
  danger = false,
  inputType = "text",
} = {}) {
  const scrim = document.getElementById("input-dialog");
  if (!scrim) return Promise.resolve(null);

  const titleEl = scrim.querySelector("#input-title");
  const bodyEl = scrim.querySelector("#input-body");
  const labelEl = scrim.querySelector("#input-label");
  const input = scrim.querySelector("#input-field");
  const helperEl = scrim.querySelector("#input-helper");
  const ok = scrim.querySelector("#input-confirm");
  const cancel = scrim.querySelector("#input-cancel");

  titleEl.textContent = title || t("common.confirm");
  bodyEl.textContent = body;
  bodyEl.hidden = !body;
  labelEl.textContent = label;
  input.type = inputType;
  input.value = value;
  helperEl.textContent = helper;
  ok.textContent = confirmLabel || t("common.confirm");
  cancel.textContent = t("common.cancel");
  ok.className = danger ? "m3-btn m3-btn--danger" : "m3-btn m3-btn--filled";

  return new Promise((resolve) => {
    const controller = new AbortController();
    const { signal } = controller;
    const close = (result) => {
      controller.abort();
      scrim.hidden = true;
      resolve(result);
    };

    const read = () => {
      const value = input.value.trim();
      return value || null;
    };

    ok.addEventListener("click", () => close(read()), { signal });
    cancel.addEventListener("click", () => close(null), { signal });
    scrim.addEventListener(
      "mousedown",
      (event) => {
        if (event.target === scrim) close(null);
      },
      { signal },
    );
    input.addEventListener(
      "keydown",
      (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          close(read());
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
    input.focus();
    input.select();
  });
}

export function confirmAction({ title = "", body = "", confirmLabel = "", danger = false } = {}) {
  const scrim = document.getElementById("confirm-dialog");
  if (!scrim) return Promise.resolve(false);

  scrim.querySelector("#confirm-title").textContent = title || t("common.confirm");
  scrim.querySelector("#confirm-body").textContent = body;
  const ok = scrim.querySelector("#confirm-ok");
  const cancel = scrim.querySelector("#confirm-cancel");
  ok.textContent = confirmLabel || t("common.confirm");
  cancel.textContent = t("common.cancel");
  ok.className = danger ? "m3-btn m3-btn--danger" : "m3-btn m3-btn--filled";

  return new Promise((resolve) => {
    const controller = new AbortController();
    const { signal } = controller;
    const close = (result) => {
      controller.abort();
      scrim.hidden = true;
      resolve(result);
    };

    ok.addEventListener("click", () => close(true), { signal });
    cancel.addEventListener("click", () => close(false), { signal });
    scrim.addEventListener(
      "mousedown",
      (event) => {
        if (event.target === scrim) close(false);
      },
      { signal },
    );
    document.addEventListener(
      "keydown",
      (event) => {
        if (event.key === "Escape") close(false);
      },
      { signal },
    );

    scrim.hidden = false;
    ok.focus();
  });
}

/** Small popup menu anchored to a button. Resolves with the picked value or null. */
export function showMenu(anchor, items) {
  const menu = document.createElement("div");
  menu.className = "m3-menu";
  menu.setAttribute("role", "menu");

  const close = (value) => {
    controller.abort();
    menu.remove();
    resolve(value);
  };

  let resolve;
  const controller = new AbortController();
  const { signal } = controller;
  const promise = new Promise((r) => {
    resolve = r;
  });

  items.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `m3-menu__item${item.selected ? " is-active" : ""}`;
    button.setAttribute("role", "menuitemradio");
    button.setAttribute("aria-checked", item.selected ? "true" : "false");

    const label = document.createElement("span");
    label.textContent = item.label;
    button.append(label);

    if (item.selected) {
      const check = document.createElement("span");
      check.className = "m3-menu__check";
      check.textContent = "✓";
      check.setAttribute("aria-hidden", "true");
      button.append(check);
    }

    button.addEventListener("click", () => close(item.value), { signal });
    menu.append(button);
  });

  document.body.append(menu);

  const anchorRect = anchor.getBoundingClientRect();
  const menuRect = menu.getBoundingClientRect();
  const rtl = document.documentElement.dir === "rtl";
  const left = rtl ? anchorRect.right - menuRect.width : anchorRect.left;
  const clampedLeft = Math.max(8, Math.min(left, window.innerWidth - menuRect.width - 8));
  let top = anchorRect.bottom + 6;
  if (top + menuRect.height > window.innerHeight - 8) {
    top = Math.max(8, anchorRect.top - menuRect.height - 6);
  }
  menu.style.left = `${clampedLeft}px`;
  menu.style.top = `${top}px`;

  document.addEventListener(
    "mousedown",
    (event) => {
      if (!menu.contains(event.target) && !anchor.contains(event.target)) close(null);
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

  return promise;
}
