"""flownotes: a self-hosted, minimal note app.

Multi-user: every account owns data/notes/<username>/ and data/uploads/<username>/,
and the search index is isolated by owner. The filesystem stays the source of truth;
SQLite is only a rebuildable index.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
import shutil
import sqlite3
import tempfile
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from anyio import open_file, to_thread
from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel

from . import database, file_ops, users, welcome

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

# Sync endpoints run in anyio's worker threads. The default limit is 40, and every scrypt
# verification inside one of them needs ~16 MiB, so a burst of sign-ins would multiply that;
# eight workers is more than a note server needs and bounds the worst case.
MAX_WORKER_THREADS = 8

# Optional: when set, /setup requires this token before creating the first administrator.
SETUP_TOKEN = os.environ.get("FLOWNOTES_SETUP_TOKEN", "").strip()
SESSION_COOKIE = "flownotes_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# Uploads are streamed in 1 MiB chunks so a 20 MB image never sits in RAM whole.
UPLOAD_CHUNK = 1024 * 1024
# SVG can carry scripts; serving it same-origin would be XSS, so it is excluded.
IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
}


def _load_trusted_proxies() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Addresses whose X-Forwarded-For header is believed (the reverse proxy in front)."""
    networks = []
    for item in os.environ.get("FLOWNOTES_TRUSTED_PROXIES", "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return networks


TRUSTED_PROXIES = _load_trusted_proxies()

FAIL_WINDOW = 300
FAIL_LIMIT = 10  # failed sign-ins per (address, account) inside the window
FAIL_IP_LIMIT = 30  # failed sign-ins per address, so spraying account names does not bypass it
MAX_THROTTLE_KEYS = 2048  # rotating account names must not grow the map without bound
_failed_logins: dict[tuple[str, str], deque[float]] = defaultdict(deque)

_last_sync: dict[str, float] = {}


def _load_secret_key() -> str:
    """Signing key: environment first, otherwise a random key persisted under data/.

    If it cannot be read or written we fall back to an in-process key. Losing sessions on
    restart is better than refusing to start.
    """
    from_env = os.environ.get("FLOWNOTES_SECRET_KEY")
    if from_env:
        return from_env
    secret_path = file_ops.DATA_DIR / ".session-secret"
    if secret_path.exists():
        try:
            existing = secret_path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except OSError:
            pass
    generated = secrets.token_urlsafe(48)
    try:
        secret_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        secret_path.write_text(generated, encoding="utf-8")
        os.chmod(secret_path, 0o600)
    except OSError:
        pass
    return generated


try:
    file_ops.ensure_dirs()
except OSError as exc:  # noqa: BLE001
    raise RuntimeError(
        f"Cannot create the data directory {file_ops.DATA_DIR}: {exc}. "
        "The mounted data/ is not writable by the user this process runs as. "
        "Start the container without `user:` so the entrypoint can chown it to "
        "FLOWNOTES_UID/FLOWNOTES_GID, or fix the ownership on the host."
    ) from exc

_serializer = URLSafeTimedSerializer(_load_secret_key(), salt="flownotes-session")


def ensure_user_dir(username: str) -> None:
    """Create a user's notes folder if it is missing, without seeding anything."""
    file_ops.user_root(username).mkdir(parents=True, exist_ok=True, mode=0o700)


def ensure_workspace(username: str, locale: str | None = None) -> None:
    """Make sure a user's notes folder exists; seed it with a welcome note when empty.

    The seed note follows the language the person signed in with, so a new account does not
    start in a language they may not read.
    """
    root = file_ops.user_root(username)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not file_ops.list_note_paths(root):
        name, body = welcome.note_for(locale)
        path, _version = file_ops.write_note(root, name, body)
        database.upsert_note_index(username, path, None, body)


def _request_locale(explicit: str, request: Request) -> str:
    """Prefer the language the page is showing, then the browser's Accept-Language header."""
    return welcome.normalise_locale(explicit or request.headers.get("accept-language"))


def migrate_legacy_into(username: str) -> int:
    """Move v1 data (sitting directly in data/notes/) into one account's folder; idempotent.

    Returns how many entries were moved. Only entries that are not an existing user
    directory are touched, so running it again is safe.
    """
    target_root = file_ops.user_root(username)
    target_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    moved = 0
    for entry in list(file_ops.NOTES_ROOT.iterdir()):
        if entry.name.startswith(".") or entry.is_symlink():
            continue
        if users.get_user(entry.name) is not None:
            continue
        target = target_root / entry.name
        if target.exists():
            continue
        shutil.move(str(entry), str(target))
        moved += 1

    uploads_root = file_ops.user_uploads(username)
    for entry in list(file_ops.UPLOADS_ROOT.iterdir()):
        if entry.name.startswith(".") or entry.is_symlink():
            continue
        if entry.is_dir() and users.get_user(entry.name) is not None:
            continue
        uploads_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = uploads_root / entry.name
        if target.exists():
            continue
        shutil.move(str(entry), str(target))
    return moved


def _first_admin() -> str | None:
    admins = sorted(
        (user for user in users.list_users() if user.is_admin), key=lambda user: user.created_at
    )
    return admins[0].username if admins else None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    to_thread.current_default_thread_limiter().total_tokens = MAX_WORKER_THREADS
    file_ops.ensure_dirs()
    try:
        database.init_db()
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            f"Cannot write the index {database.DB_PATH}: {exc}. "
            "This usually means files under data/ belong to another uid; make the owner consistent."
        ) from exc
    users.ensure_schema()

    # With no accounts we stop at /setup and create nothing automatically.
    if users.count_users() > 0:
        target = _first_admin()
        if target:
            migrate_legacy_into(target)
        for user in users.list_users():
            ensure_user_dir(user.username)
            database.sync_all_from_disk(user.username)
    yield


app = FastAPI(
    title="flownotes",
    lifespan=lifespan,
    # Disable the interactive docs: the default UI loads assets from a CDN.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    # Static files keep their ETag, so revalidation is cheap; but they must be revalidated or
    # an upgraded client would keep running the previous CSS/JS from its cache.
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    # Make the browser enforce same-origin only: the last line of defence for offline use.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    return response


def _current_user(request: Request) -> users.User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        payload = _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    user = users.get_user(str(payload.get("u") or ""))
    if user is None or user.disabled or user.token_version != int(payload.get("v") or 0):
        return None
    return user


def require_user(request: Request) -> users.User:
    user = _current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in")
    return user


def require_admin(user: users.User = Depends(require_user)) -> users.User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


def _issue_session(response, user: users.User) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        _serializer.dumps({"u": user.username, "v": user.token_version}),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        # Internal setups often use plain HTTP; secure=True would stop the cookie being sent.
        secure=False,
        path="/",
    )


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, file_ops.PathError):
        return HTTPException(status_code=400, detail=str(exc) or "Invalid path")
    if isinstance(exc, file_ops.NoteConflict):
        # The client compares versions and offers to reload or overwrite.
        return HTTPException(
            status_code=409,
            detail="This note changed on another device",
            headers={"X-Note-Version": exc.current_version},
        )
    if isinstance(exc, users.UserError):
        return HTTPException(status_code=400, detail=str(exc) or "Account operation failed")
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=409, detail=str(exc) or "Already exists")
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc) or "Not found")
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 36:
        return HTTPException(status_code=400, detail="File name or path too long")
    return HTTPException(status_code=500, detail="Operation failed")


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en" dir="ltr" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Sign in · Flow Notes</title>
<script>
  // Paint the stored theme before the stylesheet is parsed; the module at the end of the body
  // only runs after the first paint. Keep in sync with applyTheme() in ui.js.
  (() => {
    const root = document.documentElement;
    let choice = "system";
    try {
      const stored = localStorage.getItem("flownotes:theme");
      if (stored === "light" || stored === "dark" || stored === "system") choice = stored;
    } catch (error) {
      /* Storage may be blocked; the system preference is still a sane default. */
    }
    const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const resolved = choice === "system" ? (systemDark ? "dark" : "light") : choice;
    root.dataset.theme = resolved;
    root.style.colorScheme = resolved;
    try {
      const tone = localStorage.getItem("flownotes:tone");
      if (["neutral", "blue", "pink", "red", "yellow", "cyan"].includes(tone)) {
        root.dataset.tone = tone;
      }
    } catch (error) {
      /* Same as above; the neutral scheme is the default anyway. */
    }
  })();
</script>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/vendor/app.css">
</head>
<body class="auth-body">
<div class="auth-controls">
  <button class="lang-button" id="lang-button" type="button" data-i18n-title="sidebar.language" title="Language">
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><circle cx="12" cy="12" r="8.4" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M3.8 12h16.4M12 3.6c2.4 2.3 3.7 5.2 3.7 8.4S14.4 18.1 12 20.4C9.6 18.1 8.3 15.2 8.3 12S9.6 5.9 12 3.6z" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>
    <span data-lang-label>English</span>
  </button>
  <button class="icon-btn" id="theme-toggle" type="button" data-i18n-aria-label="sidebar.theme" aria-label="Toggle theme">
    <svg class="icon-sun" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><circle cx="12" cy="12" r="4.2" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M12 2.8v2M12 19.2v2M2.8 12h2M19.2 12h2M5.5 5.5l1.4 1.4M17.1 17.1l1.4 1.4M18.5 5.5l-1.4 1.4M6.9 17.1l-1.4 1.4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>
    <svg class="icon-moon" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M20 14.2A8.2 8.2 0 0 1 9.8 4a8.4 8.4 0 1 0 10.2 10.2z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>
  </button>
</div>
<main class="auth-card">
  <div class="auth-brand">
    <svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true">
      <path d="M5 6.5h14M5 12h14M5 17.5h8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>
    <h1 data-i18n="app.name">Flow Notes</h1>
  </div>
  <p class="auth-sub" data-i18n="auth.loginSubtitle">Sign in to start writing.</p>
  <form id="login-form" class="auth-form" novalidate>
    <label class="m3-field" for="username">
      <span class="m3-field__label" data-i18n="auth.username">Account</span>
      <input id="username" name="username" type="text" autocomplete="username" autofocus required>
    </label>
    <label class="m3-field" for="password">
      <span class="m3-field__label" data-i18n="auth.password">Password</span>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
    </label>
    <p class="auth-error" id="error" role="alert"></p>
    <button class="m3-btn m3-btn--filled auth-submit" type="submit" id="submit" data-i18n="auth.signIn">Sign in</button>
  </form>
</main>
<script type="module">
  import { initI18n, localeInfo, LOCALES, getLocale, setLocale, t } from "/static/i18n.js";
  import { applyTheme, applyTone, showMenu, storedTheme, storedTone, toggleExplicitTheme } from "/static/ui.js";

  initI18n();
  applyTheme(storedTheme());
  applyTone(storedTone());
  const langLabel = document.querySelector("[data-lang-label]");
  if (langLabel) langLabel.textContent = localeInfo().name;

  document.getElementById("theme-toggle").addEventListener("click", () => toggleExplicitTheme());
  document.getElementById("lang-button").addEventListener("click", async () => {
    const choice = await showMenu(
      document.getElementById("lang-button"),
      LOCALES.map((locale) => ({ value: locale.code, label: locale.name, selected: locale.code === getLocale() })),
    );
    if (!choice) return;
    setLocale(choice);
    if (langLabel) langLabel.textContent = localeInfo().name;
  });

  const form = document.getElementById("login-form");
  const errorBox = document.getElementById("error");
  const submit = document.getElementById("submit");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.textContent = "";
    submit.disabled = true;
    try {
      const response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: document.getElementById("username").value.trim(),
          password: document.getElementById("password").value,
          // The seed note and any first-run text follow the language on screen.
          lang: getLocale(),
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(response.status === 401 ? t("auth.loginFailed") : data.detail || t("auth.loginFailed"));
      window.location.href = "/";
    } catch (err) {
      errorBox.textContent = err.message || t("auth.loginFailed");
      submit.disabled = false;
    }
  });
</script>
</body>
</html>
"""


SETUP_HTML = """<!DOCTYPE html>
<html lang="en" dir="ltr" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Set up · Flow Notes</title>
<script>
  // Paint the stored theme before the stylesheet is parsed; the module at the end of the body
  // only runs after the first paint. Keep in sync with applyTheme() in ui.js.
  (() => {
    const root = document.documentElement;
    let choice = "system";
    try {
      const stored = localStorage.getItem("flownotes:theme");
      if (stored === "light" || stored === "dark" || stored === "system") choice = stored;
    } catch (error) {
      /* Storage may be blocked; the system preference is still a sane default. */
    }
    const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const resolved = choice === "system" ? (systemDark ? "dark" : "light") : choice;
    root.dataset.theme = resolved;
    root.style.colorScheme = resolved;
    try {
      const tone = localStorage.getItem("flownotes:tone");
      if (["neutral", "blue", "pink", "red", "yellow", "cyan"].includes(tone)) {
        root.dataset.tone = tone;
      }
    } catch (error) {
      /* Same as above; the neutral scheme is the default anyway. */
    }
  })();
</script>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/vendor/app.css">
</head>
<body class="auth-body">
<div class="auth-controls">
  <button class="lang-button" id="lang-button" type="button" data-i18n-title="sidebar.language" title="Language">
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><circle cx="12" cy="12" r="8.4" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M3.8 12h16.4M12 3.6c2.4 2.3 3.7 5.2 3.7 8.4S14.4 18.1 12 20.4C9.6 18.1 8.3 15.2 8.3 12S9.6 5.9 12 3.6z" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>
    <span data-lang-label>English</span>
  </button>
  <button class="icon-btn" id="theme-toggle" type="button" data-i18n-aria-label="sidebar.theme" aria-label="Toggle theme">
    <svg class="icon-sun" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><circle cx="12" cy="12" r="4.2" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M12 2.8v2M12 19.2v2M2.8 12h2M19.2 12h2M5.5 5.5l1.4 1.4M17.1 17.1l1.4 1.4M18.5 5.5l-1.4 1.4M6.9 17.1l-1.4 1.4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>
    <svg class="icon-moon" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M20 14.2A8.2 8.2 0 0 1 9.8 4a8.4 8.4 0 1 0 10.2 10.2z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>
  </button>
</div>
<main class="auth-card">
  <div class="auth-brand">
    <svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true">
      <path d="M5 6.5h14M5 12h14M5 17.5h8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>
    <h1 data-i18n="app.name">Flow Notes</h1>
  </div>
  <p class="auth-sub" data-i18n="auth.setupSubtitle">First run.</p>
  <form id="setup-form" class="auth-form" novalidate>
    <label class="m3-field" for="username">
      <span class="m3-field__label" data-i18n="auth.adminUsername">Administrator account</span>
      <input id="username" name="username" type="text" autocomplete="username" autofocus required>
    </label>
    <p class="m3-field__helper" data-i18n="auth.setupUsernameHelper">2–32 lowercase letters, digits, . _ -</p>
    <label class="m3-field" for="password">
      <span class="m3-field__label" data-i18n="auth.password">Password</span>
      <input id="password" name="password" type="password" autocomplete="new-password" required>
    </label>
    <label class="m3-field" for="confirm">
      <span class="m3-field__label" data-i18n="settings.confirmPassword">Confirm new password</span>
      <input id="confirm" name="confirm" type="password" autocomplete="new-password" required>
    </label>
    <label class="m3-field" id="token-field" for="token" hidden>
      <span class="m3-field__label" data-i18n="auth.setupToken">Setup token</span>
      <input id="token" name="token" type="password" autocomplete="off">
    </label>
    <p class="auth-error" id="error" role="alert"></p>
    <button class="m3-btn m3-btn--filled auth-submit" type="submit" id="submit" data-i18n="auth.setupSubmit">Create administrator</button>
  </form>
</main>
<script type="module">
  import { initI18n, localeInfo, LOCALES, getLocale, setLocale, t } from "/static/i18n.js";
  import { applyTheme, applyTone, showMenu, storedTheme, storedTone, toggleExplicitTheme } from "/static/ui.js";

  initI18n();
  applyTheme(storedTheme());
  applyTone(storedTone());
  const langLabel = document.querySelector("[data-lang-label]");
  if (langLabel) langLabel.textContent = localeInfo().name;

  document.getElementById("theme-toggle").addEventListener("click", () => toggleExplicitTheme());
  document.getElementById("lang-button").addEventListener("click", async () => {
    const choice = await showMenu(
      document.getElementById("lang-button"),
      LOCALES.map((locale) => ({ value: locale.code, label: locale.name, selected: locale.code === getLocale() })),
    );
    if (!choice) return;
    setLocale(choice);
    if (langLabel) langLabel.textContent = localeInfo().name;
  });

  fetch("/api/setup/status")
    .then((response) => response.json())
    .then((status) => {
      if (status.requires_token) document.getElementById("token-field").hidden = false;
      if (!status.needs_setup) window.location.href = "/login";
    })
    .catch(() => {});

  const form = document.getElementById("setup-form");
  const errorBox = document.getElementById("error");
  const submit = document.getElementById("submit");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.textContent = "";
    const password = document.getElementById("password").value;
    if (password.length < 8) {
      errorBox.textContent = t("auth.setupTooShort");
      return;
    }
    if (password !== document.getElementById("confirm").value) {
      errorBox.textContent = t("auth.setupMismatch");
      return;
    }
    submit.disabled = true;
    try {
      const response = await fetch("/api/setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: document.getElementById("username").value.trim(),
          password,
          token: document.getElementById("token").value,
          lang: getLocale(),
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || t("auth.setupFailed"));
      window.location.href = "/";
    } catch (err) {
      errorBox.textContent = err.message || t("auth.setupFailed");
      submit.disabled = false;
    }
  });
</script>
</body>
</html>
"""


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if users.count_users() == 0:
        return RedirectResponse("/setup", status_code=303)
    if _current_user(request) is not None:
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(LOGIN_HTML)


@app.get("/setup", response_class=HTMLResponse)
def setup_page():
    if users.count_users() > 0:
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(SETUP_HTML)


@app.get("/api/setup/status")
def api_setup_status():
    return {"needs_setup": users.count_users() == 0, "requires_token": bool(SETUP_TOKEN)}


class SetupPayload(BaseModel):
    username: str = ""
    password: str = ""
    token: str = ""
    lang: str = ""


@app.post("/api/setup")
def api_setup(payload: SetupPayload, request: Request):
    """Create the first administrator. Runs once; afterwards every attempt is a 409."""
    if users.count_users() > 0:
        raise HTTPException(status_code=409, detail="Already initialized, please sign in")
    if SETUP_TOKEN and not secrets.compare_digest(payload.token, SETUP_TOKEN):
        raise HTTPException(status_code=403, detail="Wrong setup token")

    try:
        created = users.create_first_admin(payload.username, payload.password)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc

    # Move single-user data into this administrator's folder, then ensure the workspace.
    moved = migrate_legacy_into(created.username)
    ensure_workspace(created.username, _request_locale(payload.lang, request))
    database.sync_all_from_disk(created.username)

    response = JSONResponse({"ok": True, "user": created.as_dict(), "migrated": moved})
    _issue_session(response, created)
    return response


class LoginPayload(BaseModel):
    username: str = ""
    password: str = ""
    lang: str = ""


def _client_host(request: Request) -> str:
    """Client address used for throttling.

    ``X-Forwarded-For`` is only honoured when the direct peer is listed in
    FLOWNOTES_TRUSTED_PROXIES; otherwise a public client could spoof its address and dodge the
    per-address limit. The header's first entry is the original client.
    """
    peer = request.client.host if request.client else ""
    if not peer:
        return "unknown"
    if not TRUSTED_PROXIES:
        return peer
    try:
        peer_address = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if not any(peer_address in network for network in TRUSTED_PROXIES):
        return peer
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    try:
        return str(ipaddress.ip_address(forwarded))
    except ValueError:
        return peer


def _throttle_key(request: Request, username: str) -> tuple[str, str]:
    return (_client_host(request), username.strip().lower())


def _prune_bucket(bucket: deque[float], now: float) -> None:
    while bucket and now - bucket[0] > FAIL_WINDOW:
        bucket.popleft()


def _prune_throttle(now: float) -> None:
    """Drop stale buckets; only runs once the map gets large, so the common path stays cheap."""
    if len(_failed_logins) <= MAX_THROTTLE_KEYS:
        return
    for key in list(_failed_logins):
        _prune_bucket(_failed_logins[key], now)
        if not _failed_logins[key]:
            _failed_logins.pop(key, None)


def _assert_not_throttled(key: tuple[str, str]) -> None:
    now = time.time()
    _prune_throttle(now)
    bucket = _failed_logins[key]
    _prune_bucket(bucket, now)
    address_bucket = _failed_logins[(key[0], "*")]
    _prune_bucket(address_bucket, now)
    if len(bucket) >= FAIL_LIMIT or len(address_bucket) >= FAIL_IP_LIMIT:
        raise HTTPException(
            status_code=429, detail="Too many failed sign-ins; try again in 5 minutes"
        )


def _record_failure(key: tuple[str, str]) -> None:
    now = time.time()
    _failed_logins[key].append(now)
    _failed_logins[(key[0], "*")].append(now)
    _prune_throttle(now)


@app.post("/api/login")
def login(payload: LoginPayload, request: Request):
    """Sync endpoint: scrypt verification is CPU-bound, so let the threadpool run it."""
    if users.count_users() == 0:
        raise HTTPException(
            status_code=409, detail="Not initialized yet; create the first administrator at /setup"
        )

    username = (payload.username or "").strip().lower()
    key = _throttle_key(request, username)
    _assert_not_throttled(key)

    user = users.authenticate(username, payload.password)
    if user is None:
        _record_failure(key)
        raise HTTPException(status_code=401, detail="Wrong account or password")

    _failed_logins.pop(key, None)
    ensure_workspace(user.username, _request_locale(payload.lang, request))
    response = JSONResponse({"ok": True, "user": user.as_dict()})
    _issue_session(response, user)
    return response


@app.post("/api/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/me")
def api_me(user: users.User = Depends(require_user)):
    return user.as_dict()


@app.post("/api/me/logout-all")
def logout_everywhere(user: users.User = Depends(require_user)):
    """Sign out every device: the account's token_version changes, so all cookies stop working."""
    try:
        users.sign_out_everywhere(user.username)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


class PasswordPayload(BaseModel):
    current_password: str = ""
    new_password: str = ""


@app.post("/api/me/password")
def change_password(payload: PasswordPayload, user: users.User = Depends(require_user)):
    if users.authenticate(user.username, payload.current_password) is None:
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if not payload.new_password:
        raise HTTPException(status_code=400, detail="The new password must not be empty")
    try:
        users.set_password(user.username, payload.new_password)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc

    refreshed = users.get_user(user.username)
    response = JSONResponse({"ok": True})
    if refreshed is not None:
        # The old cookie is invalid now, so issue a fresh one and keep this tab signed in.
        _issue_session(response, refreshed)
    return response


def _app_html(view: str) -> str:
    """Serve the app shell with its initial view baked into <html>, so a deep link to /settings
    never paints the notes layout first. Both URLs share one document and one set of scripts."""
    html = (TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
    return html.replace('data-view="notes"', f'data-view="{view}"', 1)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if users.count_users() == 0:
        return RedirectResponse("/setup", status_code=303)
    if _current_user(request) is None:
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(_app_html("notes"))


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    if users.count_users() == 0:
        return RedirectResponse("/setup", status_code=303)
    if _current_user(request) is None:
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(_app_html("settings"))


admin_api = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


class NewUserPayload(BaseModel):
    username: str = ""
    password: str = ""
    is_admin: bool = False


class UpdateUserPayload(BaseModel):
    password: str | None = None
    is_admin: bool | None = None
    disabled: bool | None = None


@admin_api.get("/users")
def admin_list_users():
    return {"users": [user.as_dict() for user in users.list_users()]}


@admin_api.post("/users")
def admin_create_user(payload: NewUserPayload):
    try:
        created = users.create_user(payload.username, payload.password, is_admin=payload.is_admin)
        ensure_user_dir(created.username)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    return {"ok": True, "user": created.as_dict()}


@admin_api.patch("/users/{username}")
def admin_update_user(
    username: str, payload: UpdateUserPayload, admin: users.User = Depends(require_admin)
):
    try:
        if payload.password:
            users.set_password(username, payload.password)
        if payload.is_admin is not None:
            users.set_admin(username, payload.is_admin)
        if payload.disabled is not None:
            if username == admin.username and payload.disabled:
                raise users.UserError("You cannot disable your own account")
            users.set_disabled(username, payload.disabled)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    updated = users.get_user(username)
    return {"ok": True, "user": updated.as_dict() if updated else None}


@admin_api.delete("/users/{username}")
def admin_delete_user(username: str, admin: users.User = Depends(require_admin)):
    if username.strip().lower() == admin.username:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    try:
        trash = users.delete_user(username)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    _last_sync.pop(username.strip().lower(), None)
    return {"ok": True, "trash": trash}


api = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


class NotePayload(BaseModel):
    path: str = ""
    content: str = ""
    # The version the client read; omit to force the write (explicit "keep mine").
    base_version: str | None = None


class FolderPayload(BaseModel):
    path: str = ""


class RenamePayload(BaseModel):
    old_path: str = ""
    new_path: str = ""


def _maybe_sync(owner: str, max_age: float = 2.0) -> None:
    """Keep the index in step with external edits (SSH, sync tools); at most once per 2 s."""
    now = time.time()
    if now - _last_sync.get(owner, 0.0) < max_age:
        return
    _last_sync[owner] = now
    try:
        database.sync_all_from_disk(owner)
    except OSError:
        pass


@api.get("/tree")
def api_tree(user: users.User = Depends(require_user)):
    _maybe_sync(user.username)
    return {"tree": file_ops.get_tree(file_ops.user_root(user.username))}


@api.get("/note")
def api_read_note(
    path: str = Query(..., min_length=1, max_length=1024), user: users.User = Depends(require_user)
):
    try:
        content, version = file_ops.read_note(file_ops.user_root(user.username), path)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    return {"path": path, "content": content, "version": version}


@api.get("/note/meta")
def api_note_meta(
    path: str = Query(..., min_length=1, max_length=1024), user: users.User = Depends(require_user)
):
    """Version of a note without its body, for “did another device change this?” checks."""
    try:
        meta = file_ops.note_meta(file_ops.user_root(user.username), path)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    return {"path": path, **meta}


@api.post("/note")
def api_create_note(payload: NotePayload, user: users.User = Depends(require_user)):
    root = file_ops.user_root(user.username)
    try:
        saved, version = file_ops.write_note(root, payload.path, payload.content, create_only=True)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    database.upsert_note_index(user.username, saved, None, payload.content)
    return {"ok": True, "path": saved, "version": version}


@api.put("/note")
def api_update_note(payload: NotePayload, user: users.User = Depends(require_user)):
    root = file_ops.user_root(user.username)
    try:
        saved, version = file_ops.write_note(
            root,
            payload.path,
            payload.content,
            base_version=payload.base_version,
            must_exist=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    database.upsert_note_index(user.username, saved, None, payload.content)
    return {"ok": True, "path": saved, "version": version}


@api.delete("/note")
def api_delete_note(
    path: str = Query(..., min_length=1, max_length=1024), user: users.User = Depends(require_user)
):
    root = file_ops.user_root(user.username)
    try:
        removed = file_ops.delete_path(root, path)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    database.delete_note_index(user.username, removed)
    return {"ok": True, "path": removed}


@api.post("/folder")
def api_create_folder(payload: FolderPayload, user: users.User = Depends(require_user)):
    try:
        created = file_ops.create_folder(file_ops.user_root(user.username), payload.path)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    return {"ok": True, "path": created}


@api.post("/rename")
def api_rename(payload: RenamePayload, user: users.User = Depends(require_user)):
    root = file_ops.user_root(user.username)
    try:
        old_rel, new_rel = file_ops.rename_path(root, payload.old_path, payload.new_path)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    database.rename_path_index(user.username, old_rel, new_rel)
    return {"ok": True, "old_path": old_rel, "new_path": new_rel}


@api.post("/upload")
async def api_upload(file: UploadFile = File(...), user: users.User = Depends(require_user)):
    extension = IMAGE_EXTENSIONS.get((file.content_type or "").lower())
    if extension is None:
        raise HTTPException(
            status_code=415, detail="Only PNG, JPEG, GIF, WebP and AVIF images are accepted"
        )

    name = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(4)}{extension}"
    root = file_ops.user_uploads(user.username)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / name
    total = 0
    try:
        async with await open_file(target, "wb") as handle:
            while chunk := await file.read(UPLOAD_CHUNK):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Image exceeds the 20 MB limit")
                await handle.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    if total == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The file is empty")
    return {"url": f"/uploads/{user.username}/{name}", "filename": name}


@api.get("/search")
def api_search(
    q: str = Query(default="", max_length=200), user: users.User = Depends(require_user)
):
    _maybe_sync(user.username)
    return {"query": q, "results": database.search_notes(user.username, q)}


@api.post("/resync")
def api_resync(user: users.User = Depends(require_user)):
    try:
        stats = database.sync_all_from_disk(user.username)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
    _last_sync[user.username] = time.time()
    return {"ok": True, **stats}


@api.get("/export")
def api_export(user: users.User = Depends(require_user)):
    """Zip the caller's notes folder; write a temp file first and then stream it."""
    import zipfile

    root = file_ops.user_root(user.username)
    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    try:
        with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as archive:
            for rel_path in file_ops.list_note_paths(root):
                archive.write(
                    file_ops.resolve_safe_path(rel_path, root=root, must_exist=True),
                    arcname=rel_path,
                )
        spool.seek(0)
    except Exception as exc:  # noqa: BLE001
        spool.close()
        raise _http_error(exc) from exc

    def iter_chunks():
        try:
            while True:
                chunk = spool.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            spool.close()

    filename = f"flownotes-{user.username}-{datetime.now():%Y%m%d-%H%M%S}.zip"
    return StreamingResponse(
        iter_chunks(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _upload_target(root: Path, filename: str) -> Path:
    """Resolve an upload name safely: a single path component that stays inside root."""
    if not filename or filename != Path(filename).name or filename.startswith("."):
        raise HTTPException(status_code=404, detail="Not found")
    resolved_root = root.resolve()
    target = (resolved_root / filename).resolve()
    if resolved_root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return target


@app.get("/uploads/{owner}/{filename}")
def serve_user_upload(owner: str, filename: str, user: users.User = Depends(require_user)):
    if owner != user.username and not user.is_admin:
        raise HTTPException(status_code=403, detail="You cannot access this file")
    try:
        root = file_ops.user_uploads(owner)
    except file_ops.PathError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    return FileResponse(_upload_target(root, filename))


@app.get("/uploads/{filename}")
def serve_legacy_upload(filename: str, user: users.User = Depends(require_user)):
    """v1 uploads have no owner folder and stay in the uploads root; signed-in users only."""
    candidates = [file_ops.user_uploads(user.username), file_ops.UPLOADS_ROOT]
    for root in candidates:
        try:
            return FileResponse(_upload_target(root, filename))
        except HTTPException:
            continue
    raise HTTPException(status_code=404, detail="Not found")


app.include_router(api)
app.include_router(admin_api)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
