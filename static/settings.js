/**
 * Settings page: account, appearance (theme + language), user management
 * (administrators only) and data. Same-origin REST calls only.
 */
import {
  apiFetch,
  apiJson,
  applyTheme,
  applyTone,
  askText,
  confirmAction,
  showSnackbar,
  storedTheme,
  storedTone,
  TONES,
  watchSystemTheme,
} from "./ui.js";
import { LOCALES, getLocale, setLocale, t } from "./i18n.js";

const ICONS = {
  key: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><circle cx="8" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M11.2 12H20M17 12v3M14.5 12v2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  shield:
    '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M12 4l7 2.6v5.2c0 4.2-2.9 7-7 8.2-4.1-1.2-7-4-7-8.2V6.6z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>',
  pause:
    '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M9.5 6.5v11M14.5 6.5v11" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  play: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M9 6.5l9 5.5-9 5.5z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>',
  trash:
    '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M5 7h14M9.5 7V5.2h5V7M6.8 7l.6 12h9.2l.6-12" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

const els = {
  accountName: document.getElementById("account-name"),
  accountCreated: document.getElementById("account-created"),
  accountRole: document.getElementById("account-role"),
  passwordForm: document.getElementById("password-form"),
  currentPassword: document.getElementById("current-password"),
  newPassword: document.getElementById("new-password"),
  confirmPassword: document.getElementById("confirm-password"),
  passwordHelper: document.getElementById("password-helper"),
  themeSegmented: document.getElementById("theme-segmented"),
  languageSelect: document.getElementById("language-select"),
  toneRow: document.getElementById("tone-row"),
  usersCard: document.getElementById("users-card"),
  userList: document.getElementById("user-list"),
  addUser: document.getElementById("btn-add-user"),
  exportButton: document.getElementById("btn-settings-export"),
  resync: document.getElementById("btn-resync"),
  logout: document.getElementById("btn-logout"),
};

let me = null;
let usersCache = [];
let watchingSystemTheme = false;

function markThemeChoice(choice) {
  els.themeSegmented.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.setAttribute("aria-checked", String(button.dataset.themeChoice === choice));
  });
}

const TONE_KEYS = {
  neutral: "tone.neutral",
  blue: "tone.blue",
  pink: "tone.pink",
  red: "tone.red",
  yellow: "tone.yellow",
  cyan: "tone.cyan",
};

const TONE_COLORS = {
  neutral: "#1b1b1b",
  blue: "#1a5fb4",
  pink: "#9d3b62",
  red: "#a4342a",
  yellow: "#6f5300",
  cyan: "#00687a",
};

function renderToneRow() {
  const current = storedTone();
  els.toneRow.textContent = "";
  TONES.forEach((tone) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tone-swatch";
    button.dataset.toneChoice = tone;
    button.setAttribute("role", "radio");
    button.setAttribute("aria-checked", String(tone === current));

    const dot = document.createElement("span");
    dot.className = "tone-swatch__dot";
    dot.style.setProperty("--tone-color", TONE_COLORS[tone]);
    dot.setAttribute("aria-hidden", "true");

    const label = document.createElement("span");
    label.textContent = t(TONE_KEYS[tone]);

    button.append(dot, label);
    els.toneRow.append(button);
  });
}

function fillLanguageSelect() {
  els.languageSelect.textContent = "";
  LOCALES.forEach((locale) => {
    const option = document.createElement("option");
    option.value = locale.code;
    option.textContent = locale.name;
    els.languageSelect.append(option);
  });
  els.languageSelect.value = getLocale();
}

function formatDate(epochSeconds) {
  if (!epochSeconds) return "";
  try {
    return new Date(epochSeconds * 1000).toLocaleDateString(getLocale(), {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  } catch (error) {
    return "";
  }
}

function applyAccountText() {
  if (!me) return;
  els.accountName.textContent = me.username;
  els.accountCreated.textContent = me.created_at
    ? t("settings.createdAt", { date: formatDate(me.created_at) })
    : "";
  els.accountRole.textContent = me.is_admin ? t("settings.role.admin") : t("settings.role.user");
  els.accountRole.classList.toggle("chip--muted", !me.is_admin);
}

async function loadMe() {
  me = await apiJson("/api/me");
  applyAccountText();
  if (me.is_admin) {
    els.usersCard.hidden = false;
    await loadUsers();
  }
  return me;
}

async function loadUsers() {
  const data = await apiJson("/api/admin/users");
  usersCache = Array.isArray(data.users) ? data.users : [];
  renderUsers(usersCache);
}

function actionButton(icon, label, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "icon-btn";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.innerHTML = icon;
  button.addEventListener("click", () => void handler());
  return button;
}

function renderUsers(users) {
  els.userList.textContent = "";
  users.forEach((user) => {
    const row = document.createElement("div");
    row.className = "user-row";

    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.textContent = user.username.slice(0, 1).toUpperCase();

    const text = document.createElement("div");
    text.className = "user-row__text";
    const name = document.createElement("p");
    name.className = "user-row__name";
    name.textContent = user.username + (user.username === me?.username ? ` ${t("settings.you")}` : "");
    const meta = document.createElement("p");
    meta.className = "user-row__meta";
    meta.textContent = [
      user.is_admin ? t("settings.role.admin") : t("settings.role.user"),
      user.disabled ? t("settings.status.disabled") : t("settings.status.active"),
    ].join(" · ");
    text.append(name, meta);

    const actions = document.createElement("div");
    actions.className = "user-row__actions";
    actions.append(
      actionButton(ICONS.key, t("settings.action.resetPassword"), () => resetPassword(user)),
      actionButton(
        ICONS.shield,
        user.is_admin ? t("settings.action.demote") : t("settings.action.promote"),
        () => toggleAdmin(user),
      ),
      actionButton(
        user.disabled ? ICONS.play : ICONS.pause,
        user.disabled ? t("settings.action.enable") : t("settings.action.disable"),
        () => toggleDisabled(user),
      ),
      actionButton(ICONS.trash, t("settings.action.delete"), () => removeUser(user)),
    );

    row.append(avatar, text, actions);
    els.userList.append(row);
  });
}

async function patchUser(username, payload) {
  await apiFetch(`/api/admin/users/${encodeURIComponent(username)}`, { method: "PATCH", body: payload });
  await loadUsers();
}

async function resetPassword(user) {
  const password = await askText({
    title: t("settings.reset.title"),
    label: t("settings.reset.label", { user: user.username }),
    inputType: "password",
    helper: t("settings.reset.helper"),
    confirmLabel: t("settings.reset.title"),
  });
  if (!password) return;
  try {
    await patchUser(user.username, { password });
    showSnackbar(t("toast.passwordReset", { user: user.username }));
  } catch (error) {
    showSnackbar(error.message);
  }
}

async function toggleAdmin(user) {
  const promote = !user.is_admin;
  const ok = await confirmAction({
    title: promote ? t("settings.promote.title") : t("settings.demote.title"),
    body: promote
      ? t("settings.promote.body", { user: user.username })
      : t("settings.demote.body", { user: user.username }),
    confirmLabel: promote ? t("settings.promote.title") : t("settings.action.demote"),
  });
  if (!ok) return;
  try {
    await patchUser(user.username, { is_admin: promote });
    showSnackbar(t("toast.permissionUpdated"));
  } catch (error) {
    showSnackbar(error.message);
  }
}

async function toggleDisabled(user) {
  const disabling = !user.disabled;
  const ok = await confirmAction({
    title: disabling ? t("settings.disable.title") : t("settings.enable.title"),
    body: disabling
      ? t("settings.disable.body", { user: user.username })
      : t("settings.enable.body", { user: user.username }),
    confirmLabel: disabling ? t("settings.action.disable") : t("settings.action.enable"),
    danger: disabling,
  });
  if (!ok) return;
  try {
    await patchUser(user.username, { disabled: disabling });
    showSnackbar(t(disabling ? "toast.userDisabled" : "toast.userEnabled"));
  } catch (error) {
    showSnackbar(error.message);
  }
}

async function removeUser(user) {
  const ok = await confirmAction({
    title: t("settings.delete.title"),
    body: t("settings.delete.body", { user: user.username }),
    confirmLabel: t("settings.action.delete"),
    danger: true,
  });
  if (!ok) return;
  try {
    await apiJson(`/api/admin/users/${encodeURIComponent(user.username)}`, { method: "DELETE" });
    await loadUsers();
    showSnackbar(t("toast.userDeleted", { user: user.username }), { timeout: 8000 });
  } catch (error) {
    showSnackbar(error.message);
  }
}

async function addUser() {
  const username = await askText({
    title: t("settings.addUser"),
    label: t("settings.addUser.username"),
    helper: t("settings.addUser.helper"),
    confirmLabel: t("common.next"),
  });
  if (!username) return;
  const password = await askText({
    title: t("settings.addUser"),
    body: t("settings.addUser.passwordBody", { user: username }),
    label: t("settings.newPassword"),
    inputType: "password",
    helper: t("settings.passwordHint"),
    confirmLabel: t("common.create"),
  });
  if (!password) return;
  try {
    await apiFetch("/api/admin/users", { method: "POST", body: { username, password } });
    await loadUsers();
    showSnackbar(t("toast.userCreated", { user: username }));
  } catch (error) {
    showSnackbar(error.message);
  }
}

els.addUser.addEventListener("click", () => void addUser());

els.passwordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const current = els.currentPassword.value;
  const next = els.newPassword.value;
  const confirmation = els.confirmPassword.value;
  els.passwordHelper.classList.remove("is-error");

  if (next.length < 8) {
    els.passwordHelper.textContent = t("settings.passwordTooShort");
    els.passwordHelper.classList.add("is-error");
    return;
  }
  if (next !== confirmation) {
    els.passwordHelper.textContent = t("settings.passwordMismatch");
    els.passwordHelper.classList.add("is-error");
    return;
  }

  try {
    await apiFetch("/api/me/password", {
      method: "POST",
      body: { current_password: current, new_password: next },
    });
    els.passwordForm.reset();
    els.passwordHelper.textContent = t("settings.passwordUpdated");
    showSnackbar(t("toast.passwordUpdated"));
  } catch (error) {
    els.passwordHelper.textContent = error.message;
    els.passwordHelper.classList.add("is-error");
  }
});

els.toneRow.addEventListener("click", (event) => {
  const button = event.target.closest("[data-tone-choice]");
  if (!button) return;
  applyTone(button.dataset.toneChoice);
  renderToneRow();
});

els.themeSegmented.addEventListener("click", (event) => {
  const button = event.target.closest("[data-theme-choice]");
  if (!button) return;
  const choice = button.dataset.themeChoice;
  applyTheme(choice);
  markThemeChoice(choice);
});

els.languageSelect.addEventListener("change", () => {
  setLocale(els.languageSelect.value);
});

els.exportButton.addEventListener("click", () => {
  window.location.href = "/api/export";
});

els.resync.addEventListener("click", async () => {
  try {
    const data = await apiJson("/api/resync", { method: "POST" });
    showSnackbar(t("toast.indexSynced", { updated: data.updated, removed: data.removed }));
  } catch (error) {
    showSnackbar(error.message);
  }
});

els.logout.addEventListener("click", async () => {
  try {
    await apiFetch("/api/logout", { method: "POST" });
  } catch (error) {
    /* Still navigate away. */
  }
  window.location.href = "/login";
});

document.addEventListener("localechange", () => {
  applyAccountText();
  if (usersCache.length || els.usersCard.hidden === false) renderUsers(usersCache);
  fillLanguageSelect();
  renderToneRow();
  if (!els.passwordHelper.classList.contains("is-error")) {
    els.passwordHelper.textContent = t("settings.passwordHint");
  }
});

/** Called by app.js whenever the settings view is opened; safe to call repeatedly. */
export async function initSettings() {
  fillLanguageSelect();
  renderToneRow();
  markThemeChoice(storedTheme());
  if (!watchingSystemTheme) {
    watchingSystemTheme = true;
    watchSystemTheme(() => markThemeChoice(storedTheme()));
  }
  try {
    await loadMe();
  } catch (error) {
    showSnackbar(error.message);
  }
}
