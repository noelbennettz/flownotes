# Flow Notes — technical documentation

This file is the reference: installation detail, data layout, internals, the API, operations
and troubleshooting. The [README](README.md) is the short version.

## What it is

A single-process Python service that serves a Markdown editor over HTTP. Notes are plain
`.md` files under `data/notes/<account>/`, the folder tree on disk *is* the sidebar, and
SQLite holds the accounts plus a full-text index that can be dropped and rebuilt at any time.
The browser bundle is built into the repository, so the application makes no outbound
requests: no CDN, no web fonts, no remote icons.

## Requirements

- Docker with Compose (recommended), or Python 3.11+ for a manual install.
- About 170 MB of disk for the image, plus whatever the notes take.
- A reverse proxy that terminates TLS if the service is not on a trusted LAN.

## Install

### Docker Compose

```bash
mkdir -p data
docker compose up -d --build
```

The shipped `docker-compose.yml` mounts `./data`, maps `8000:8000`, runs as
`FLOWNOTES_UID:FLOWNOTES_GID` (default `1000:1000`) and sets `restart: unless-stopped`.

Two variants are included: [docker-compose.example.yml](docker-compose.example.yml) documents
every knob, and [docker-compose.ghcr.yml](docker-compose.ghcr.yml) pulls the published image
instead of building it.

### Published image

```bash
docker pull ghcr.io/noelbennettz/flownotes:latest
docker run -d --name flownotes -p 8000:8000 \
  -v "$PWD/data:/app/data" ghcr.io/noelbennettz/flownotes:latest
```

The package is private by default, so pulling needs `docker login ghcr.io` with a token that
can read packages.

### Manual

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
FLOWNOTES_DATA="$PWD/data" uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## First run

With no accounts the service answers `/setup` on `/`, `/login` and `/settings`. Creating the
first administrator there also claims any single-user data left directly under
`data/notes/`: those entries move into `data/notes/<account>/`, and the old images move into
`data/uploads/<account>/`.

If `/setup` may be reachable by others before you finish, set `FLOWNOTES_SETUP_TOKEN`; the page
then requires that value before it creates anything. The endpoint accepts exactly one
successful call — afterwards it returns `409`.

Accounts are created from **Settings → Users**. Names are lowercase letters, digits, `.`, `_`
and `-`, 2–32 characters, and they double as the folder name, so they cannot be renamed later.

## Data layout

```
data/
├── notes/<account>/…          # plain Markdown; folders are the tree
├── uploads/<account>/…        # pasted or uploaded images
├── index.db                   # users, notes_fts, notes_state
├── .session-secret            # cookie signing key, mode 0600
└── trash/<account>-<stamp>/   # notes/uploads moved here when an account is deleted
```

Notes and image folders are created with mode `0700`. `index.db` (which stores note text) and
`.session-secret` are `0600`, so other local users cannot read them either. Backups can be as
simple as copying `data/` while the service is stopped, or `rsync` of `data/notes/` at any time —
the `.md` files are always complete because writes go through a temporary file and `os.replace`.

## How the index works

`notes_fts` is an FTS5 table with `unicode61` tokenisation and the columns
`owner, path, title, content`; `notes_state` stores `(owner, path, mtime, size)`.

- `GET /api/tree` and `GET /api/search` trigger an incremental sync (at most once every two
  seconds per account), so editing files over SSH shows up without a restart.
- `POST /api/resync` forces a full comparison and reports how many notes were updated or
  removed.
- `init_db()` drops and recreates both tables if an older schema without `owner` is found. The
  index is derived data; deleting `index.db` is always safe, but it also deletes the accounts,
  so export the notes first.
- Search uses `MATCH` with quoted tokens and `snippet()` for the `<mark>` excerpt. `unicode61`
  treats a run of CJK characters as one token, so a query that is not a prefix of that run finds
  nothing through FTS; when FTS returns no rows the code falls back to a `LIKE` substring scan
  and builds the excerpt in Python.

## Editor and Markdown

- Tiptap 2 with `tiptap-markdown`; content is stored as Markdown, not HTML. Headings 1–6,
  bullet/ordered/task lists, code blocks, blockquotes, horizontal rules, bold, italic,
  strikethrough, inline code and images round-trip.
- Input rules convert `# `, `- `, `1. `, `> `, `---` and a fence (` ``` ` + space) as you type.
  `/` opens the command menu (arrows, Enter, Esc also work); `/image` opens a file picker and
  uploads the picture.
- Pasting an image uploads it to `data/uploads/<account>/` and inserts
  `![](/uploads/<account>/<file>)`.
- Autosave runs 600 ms after the last keystroke; switching notes flushes first, and
  `pagehide`/`visibilitychange` send the pending content with `keepalive`.
- The contenteditable carries `dir="auto"` and `unicode-bidi: plaintext`, so an English note
  stays left-to-right even when the interface is Arabic, and an Arabic note is right-to-left.

## Interface

Seven locales: English, Traditional Chinese, Simplified Chinese, Arabic, French, Russian and
Spanish. The choice is stored in `localStorage` (`flownotes:lang`) and applies to the login and
setup pages too. Themes (`flownotes:theme`: light, dark, system) and accent tones
(`flownotes:tone`: neutral, blue, pink, red, yellow, cyan) are per browser as well.

Layout uses logical CSS properties, so `dir="rtl"` mirrors the drawer, tree indentation, the
breadcrumb and the floating controls. On screens narrower than 900 px the drawer becomes an
overlay with a scrim. On pointers without hover (`@media (hover: none)`, i.e. touch) the tree
actions are always visible and rows grow to 44 px, whatever the window width.

## API

All `/api/*` routes except `/api/login`, `/api/logout` and `/api/setup*` require the session
cookie. Payloads are JSON; errors are `{"detail": "…"}` with a matching status code.

| Method | Path | Body / query | Notes |
| --- | --- | --- | --- |
| GET | `/api/setup/status` | — | `{needs_setup, requires_token}` |
| POST | `/api/setup` | `{username, password, token}` | Creates the first administrator; succeeds once |
| POST | `/api/login` | `{username, password}` | Sets the cookie; 10 failures per IP+account per 5 min → `429` |
| POST | `/api/logout` | — | Clears the cookie |
| GET | `/api/me` | — | Current account |
| POST | `/api/me/password` | `{current_password, new_password}` | Bumps `token_version`, re-issues the cookie |
| GET | `/api/tree` | — | Nested `{name, path, type, mtime, children}` |
| GET | `/api/note` | `?path=` | `{path, content}` |
| POST | `/api/note` | `{path, content}` | Creates; `409` if it exists |
| PUT | `/api/note` | `{path, content}` | Updates; `404` if missing |
| DELETE | `/api/note` | `?path=` | File or folder (recursive) |
| POST | `/api/folder` | `{path}` | Creates nested folders |
| POST | `/api/rename` | `{old_path, new_path}` | Rename and/or move; `409` on collision |
| POST | `/api/upload` | multipart `file` | PNG/JPEG/GIF/WebP/AVIF, 20 MB; `{url, filename}` |
| GET | `/api/search` | `?q=` | Up to 20 `{path, title, snippet}` |
| POST | `/api/resync` | — | `{updated, removed}` |
| GET | `/api/export` | — | ZIP of the account's notes (images are not included) |
| GET | `/api/admin/users` | — | Administrators only |
| POST | `/api/admin/users` | `{username, password, is_admin}` | Administrators only |
| PATCH | `/api/admin/users/{name}` | `{password?, is_admin?, disabled?}` | Administrators only |
| DELETE | `/api/admin/users/{name}` | — | Moves the account's files to `data/trash/` |

Uploads are served from `/uploads/<account>/<file>` and require a session; only the owner or an
administrator may read them. Legacy `/uploads/<file>` links (from the single-user layout) have no
owner on disk, so any signed-in account can read them — move them into an account folder (and
rewrite the links) if that matters for your deployment.

## Operations

### Reset a password

```bash
docker compose exec -it flownotes python -m app.cli list
docker compose exec -it flownotes python -m app.cli passwd admin
```

The CLI writes `data/index.db` directly; the change takes effect immediately (no restart) and
invalidates that account's sessions. `create <account>` works only while there are no accounts.

### Upgrade

```bash
docker compose pull        # with docker-compose.ghcr.yml, or:
docker compose up -d --build
```

The index migrates itself; accounts and notes live in `./data` and are untouched. Back up
`data/` first if the release notes mention a schema change.

### Delete an account

**Settings → Users → Delete** moves `notes/<name>/` and `uploads/<name>/` into
`data/trash/<name>-<timestamp>/` and drops the account. Nothing in `trash/` is served; delete
the directory when you are sure.

### Move the deployment

Stop the container, copy `data/` (including `.session-secret` to keep sessions), start it on the
new host with the same `FLOWNOTES_DATA` contents. Nothing else is stored outside the image.

## Security notes

- Sessions: `flownotes_session`, signed with `FLOWNOTES_SECRET_KEY` or the generated
  `data/.session-secret`, `HttpOnly`, `SameSite=Lax`, `Path=/`, 7 days. `Secure` is off on
  purpose so a LAN address over plain HTTP can log in; terminate TLS in front.
- `token_version` per account invalidates cookies when a password changes or an account is
  disabled.
- Passwords are `scrypt` (N=2^14, r=8, p=1) with a random salt; unknown accounts still pay for
  one hash so response time does not reveal whether a name exists.
- Every filesystem path is resolved by `resolve_safe_path`: it rejects absolute paths, `..`,
  control characters, components over 255 bytes, paths over 1024 characters, and anything whose
  `resolve()` lands outside the account's root (symlinks included).
- The CSP sent with every response is `default-src 'self'`, which also blocks a stray external
  reference from loading.
- Uploads: MIME allow-list (no SVG, because scripts), server-generated names, 20 MB cap.
- The setup wizard creates the first administrator without authentication, which is why
  `FLOWNOTES_SETUP_TOKEN` exists. Set it before exposing the port.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| The container restarts with `Permission denied: '/app/data/notes'` | `./data` is not writable by the container user. On rootless Docker set `FLOWNOTES_UID=0`/`FLOWNOTES_GID=0` in `.env`, or `chmod 777 data`. |
| `429` on login | The rate limit for that IP and account is full; wait five minutes. Restarting the service clears it. |
| A note edited over SSH does not appear in search | Wait two seconds and refresh the tree, or run `POST /api/resync`. |
| Searching a Chinese word finds nothing | FTS tokenises whole CJK runs; the LIKE fallback runs only when FTS returns zero rows. Try a shorter prefix. |
| Upload rejected with `415` | Only PNG, JPEG, GIF, WebP and AVIF are accepted. |
| Images 403 | You are signed in as another account; only the owner or an administrator may read them. |
| `/setup` is gone | At least one account exists. Use `/login`, or `python -m app.cli passwd` for a reset. |
| Everything looks unstyled | `static/vendor/app.css` or the route is blocked by a proxy; check the browser console instead of the app logs. |

## Development

```bash
docker compose up -d --build
python3 -m py_compile app/*.py
cd vendor-build && npm ci && node build.mjs   # only when the editor deps change
```

CI (`.github/workflows/docker.yml`) builds a multi-arch image, pushes it to GHCR and then boots
it to run the setup/login/write/search smoke test. It also prunes old runs so the Actions tab
stays small. See [CONTRIBUTING.md](CONTRIBUTING.md) for the checks to run locally.

## Licence

MIT; see [LICENSE](LICENSE). Third-party components and their licences are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
