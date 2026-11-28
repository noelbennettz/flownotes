# Flow Notes documentation

> This tree documents the current `master`, published as
> `ghcr.io/noelbennettz/flownotes:latest` (also tagged `master` and
> `sha-<commit>`). There are no numbered releases, so there is no per-version
> documentation.

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
- About 170 MB of disk for the image (about 51 MB compressed), plus whatever the notes take.
- A reverse proxy that terminates TLS if the service is not on a trusted LAN.

The Python process idles at roughly 50–60 MB RSS and stays under about 100 MB through a burst of
parallel sign-ins, uploads and large-note reads: uploads are streamed in 1 MiB chunks, SQLite keeps
a 1 MiB page cache per connection, and the request threadpool is capped at eight workers so
scrypt's 16 MiB-per-hash cannot multiply without bound. The image also sets `MALLOC_ARENA_MAX=2`,
which keeps glibc from retaining one malloc arena per worker thread.

## Install

### Docker Compose

```bash
mkdir -p data
docker compose up -d --build
```

The shipped `docker-compose.yml` mounts `./data`, maps `8000:8000`, and sets
`FLOWNOTES_UID:FLOWNOTES_GID` (default `1000:1000`) plus `restart: unless-stopped`. The container's
entrypoint starts as root, hands `./data` to that uid/gid, and drops to it before uvicorn runs, so
the directory stays writable and the Markdown files stay editable even when Docker created the
mount root as root. Setting both ids to `0` (the rootless-Docker case) skips the chown and the
drop: the process stays root and the files end up owned by the host user that subuid maps to. Do
not add `user:` to the service: that skips the chown.

Two variants are included: [docker-compose.example.yml](docker-compose.example.yml) documents
every knob, and [docker-compose.ghcr.yml](docker-compose.ghcr.yml) pulls the published image
instead of building it.

### Published image

```bash
docker pull ghcr.io/noelbennettz/flownotes:latest
docker run -d --name flownotes -e FLOWNOTES_UID="$(id -u)" -e FLOWNOTES_GID="$(id -g)" \
  -p 8000:8000 -v "$PWD/data:/app/data" ghcr.io/noelbennettz/flownotes:latest
```

The entrypoint chowns `./data` to `FLOWNOTES_UID`/`FLOWNOTES_GID` and then runs as that user, so
the Markdown files belong to you. Omit the variables to get `1000:1000`, or set both to `0` on
rootless Docker (see [Troubleshooting](#troubleshooting)). Passing `--user` also works but
disables the chown, which is the old behaviour and only keeps working if `./data` is already
writable by that uid.

The package is public and anonymous pulls work, so `docker login ghcr.io` is not required. The
images are multi-architecture (`linux/amd64` and `linux/arm64`) and tagged `latest`, `master` and
`sha-<commit>`.

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
The last active administrator cannot be demoted, disabled or deleted, and nobody can disable or
delete their own account; those buttons are disabled in the UI (with the reason as the tooltip)
and the API returns `400` for the same operations. A single-account instance therefore keeps its
only administrator intact while still allowing new accounts to be added.

A new account gets one seed note on its first sign-in, written in the language the sign-in page
was showing: `Welcome.md`, `歡迎使用.md`, `欢迎使用.md`, `مرحبا.md`, `Bienvenue.md`,
`Добро-пожаловать.md` or `Bienvenido.md`. If the request carries no language the browser's
`Accept-Language` decides, and anything unrecognised falls back to English. The note is an
ordinary file afterwards — rename it or delete it.

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
  strikethrough, inline code and images round-trip. A single newline stays a newline instead of
  being folded into a paragraph the first time the note is edited (`breaks: true`, and the
  serialiser's line-continuation backslash is removed again outside fenced code), so saving
  does not silently reflow a hand-written file.
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
| POST | `/api/setup` | `{username, password, token, lang}` | Creates the first administrator; succeeds once |
| POST | `/api/login` | `{username, password, lang}` | Sets the cookie; 10 failures per IP+account, 30 per IP, per 5 min → `429` |
| POST | `/api/logout` | — | Clears the cookie |
| GET | `/api/me` | — | Current account |
| POST | `/api/me/password` | `{current_password, new_password}` | Bumps `token_version`, re-issues the cookie |
| POST | `/api/me/logout-all` | — | Bumps `token_version`: every session ends, including the caller's |
| GET | `/api/tree` | — | Nested `{name, path, type, mtime, children}` |
| GET | `/api/note` | `?path=` | `{path, content, version}` |
| GET | `/api/note/meta` | `?path=` | `{path, version, mtime, size}`; no body |
| POST | `/api/note` | `{path, content}` | Creates; `409` if it exists |
| PUT | `/api/note` | `{path, content, base_version?, base_content?}` | Updates; merges when `base_version` is stale, `409` without `base_content`, `404` if missing |
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

### Saving and concurrent edits

Flow Notes is not a collaborative editor. The same account can be signed in on several devices,
but there is no live sync, no presence and no cursor sharing, and editing one note from two
devices at the same time is not supported — the saves race, and without the checks below one of
them would simply be lost. The versioning described here is a safety net for that race, not a
feature to rely on for teamwork.

`GET /api/note` returns a content-hash `version`. The browser sends it back as `base_version`
together with the text it last saw as `base_content`; the hash of `base_content` is compared with
`base_version` to verify the base, and the save happens atomically under the write lock. If the
file changed in the meantime — another device, another tab, an SSH edit picked up by the index, or
a note that was renamed or deleted — the server performs a line-based three-way merge of base,
stored text and the incoming text. Edits in different places are combined and the merged text is
written; the next save on either side continues from the merged file. Two devices changing the
same lines differently cannot be merged, so the note keeps the version that was already on disk
and the incoming text is saved next to it as `Name (conflict YYYY-MM-DD HH-MM-SS).md`; nothing is
discarded and both notes appear in the tree, search and export. The same copy is made when the
note was renamed or deleted before the save landed, or when the base cannot be verified (a stale
or lossy copy, e.g. a file that is not valid UTF-8) — merging on an unverified base would be a
guess.

A save without `base_content` (older clients, API calls, and the tab-close keepalive write, whose
64 KiB body limit cannot carry a second copy of the text) still gets `409` with the current
version in `X-Note-Version`; returning to the tab then adopts the stored text as the merge base
and saves on top of it. The response also carries `sent_version`, the hash of the submitted text:
a client that kept typing while the save was in flight uses it as the next `base_version`, so
those keystrokes are merged on top of the stored result instead of overwriting it. Omitting
`base_version` forces the write, which is available to API callers but no longer used by the
browser. Returning to a tab also re-checks `/api/note/meta`, so an idle note picks up edits made
elsewhere; a note that was renamed or deleted elsewhere is cleared instead of being saved into
nothing (unless there were unsaved edits, which become a conflict copy).

“Sign out everywhere” in Settings calls `/api/me/logout-all`, which bumps `token_version`: every
cookie for the account stops working, including the one in the current browser.

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

Static assets answer `Cache-Control: no-cache` with an ETag, so an open tab picks up a new
`app.css` or bundle on the next reload instead of running the previous one from its cache.

### Delete an account

**Settings → Users → Delete** moves `notes/<name>/` and `uploads/<name>/` into
`data/trash/<name>-<timestamp>/` and drops the account. Nothing in `trash/` is served; delete
the directory when you are sure.

### Move the deployment

Stop the container, copy `data/` (including `.session-secret` to keep sessions), start it on the
new host with the same `FLOWNOTES_DATA` contents. Nothing else is stored outside the image.

## Removing it

`./data` is a bind mount, not a named volume, so compose never deletes it. What
each command removes:

| Command | Removes | Keeps |
| --- | --- | --- |
| `docker compose stop` | the running container | the container, `./data`, the image |
| `docker compose down` | the container and the compose network | `./data`, the image, the compose file |
| `docker compose down -v` | the above and any named volumes the file declares | `./data` — it is a bind mount, remove it yourself |
| `docker compose down --rmi all` | the container, the network and the image | `./data` |
| `rm -rf data/` | every note, every upload, the index, the trash and the session secret | the image, the compose file |

`rm -rf data/` cannot be undone: the `.md` files are the only copy of the notes,
and `index.db` is an index over them that can be rebuilt, not a backup. Copy
`data/` while the service is stopped first ([Data layout](#data-layout)).

Deleting an account is a different operation: **Settings → Users → Delete** moves
its notes and uploads into `data/trash/` and leaves them there until you delete
that directory.

Nothing else is written: no cron job, no systemd unit, no file outside `./data`
and the container, and the published port carries no firewall rules of its own.
The reverse proxy in front, if there is one, is a separate configuration to
remove.

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
- The CSP sent with every response is same-origin: `default-src 'self'`, `img-src 'self' data:
  blob:`, `style-src`/`script-src 'self' 'unsafe-inline'` for the inline theme script and styles,
  plus `object-src 'none'`, `base-uri 'none'`, `form-action 'self'` and `frame-ancestors 'none'`.
  No external origin is allowed to load anything.
- Uploads: MIME allow-list (no SVG, because scripts), server-generated names, 20 MB cap.
- The setup wizard creates the first administrator without authentication, which is why
  `FLOWNOTES_SETUP_TOKEN` exists. Set it before exposing the port.

## TLS and reverse proxies

Flow Notes serves plain HTTP: uvicorn listens on port 8000 and the image carries no TLS
certificate, no ACME client and no HSTS header. That is deliberate — the session cookie is
`HttpOnly` and `SameSite=Lax` but not `Secure`, so `http://192.168.1.10:8000` can sign in. Keep the
port on the LAN, or terminate TLS in front and leave the app as it is. Nothing in the app depends
on the scheme: there are no absolute URLs, no WebSocket upgrade and no redirect to https.

Bind the published port to loopback so only the proxy can reach it:

```yaml
ports:
  - "127.0.0.1:8000:8000"
```

Caddy obtains and renews the certificate by itself:

```
notes.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

nginx with a certificate from anywhere else — the body size matters because uploads are capped at
20 MB, and `X-Forwarded-For` is what lets the login throttle see the real client (see below):

```nginx
server {
    listen 443 ssl;
    server_name notes.example.com;
    client_max_body_size 21m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

### Trusted proxies and client addresses

Failed sign-ins are counted per client address (10 per account, 30 per address in five minutes).
Behind a reverse proxy the app would only ever see the proxy's address, so tell it which peers may
be believed: set `FLOWNOTES_TRUSTED_PROXIES` to a comma-separated list of IPs or CIDRs. Only when
the direct peer matches that list does the app read the first `X-Forwarded-For` entry; otherwise
the header is ignored, so a client reaching uvicorn directly cannot spoof its address to dodge the
limit. Caddy sets `X-Forwarded-For` itself; nginx needs the line above.

In Docker the proxy reaches the container from the bridge gateway (commonly `172.17.0.1` for the
default bridge, `172.18.0.1` for a compose network), not from `127.0.0.1` — check with
`docker network inspect <network>`. Leave the variable empty when the port is published directly.

If you want HSTS or a redirect from http to https, add it at the proxy; the app does not send
either, and a browser that learned HSTS for the bare IP would lock you out of plain-HTTP access.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| The container restarts with `Permission denied: '/app/data/notes'` | The entrypoint could not take ownership. It only chowns when the process starts as root, so remove `user:` from the compose service (or `--user` from `docker run`) and let `FLOWNOTES_UID`/`FLOWNOTES_GID` do the work. If the mount itself refuses `chown` (NFS root squash, read-only bind), fix it on the host: `sudo chown -R 1000:1000 data` or `chmod 777 data`. On rootless Docker set `FLOWNOTES_UID=0`/`FLOWNOTES_GID=0` in `.env`. |
| `429` on login | The rate limit for that IP and account is full; wait five minutes. Restarting the service clears it. |
| A note edited over SSH does not appear in search | Wait two seconds and refresh the tree, or run `POST /api/resync`. |
| Searching a Chinese word finds nothing | FTS tokenises whole CJK runs; the LIKE fallback runs only when FTS returns zero rows. Try a shorter prefix. |
| Upload rejected with `415` | Only PNG, JPEG, GIF, WebP and AVIF are accepted. |
| Images 403 | You are signed in as another account; only the owner or an administrator may read them. |
| A note appeared as `Name (conflict …).md` | Two saves touched the same lines. The original keeps the file that was on disk and the copy holds the other text; merge them by hand and delete the copy. This is a race fallback, not collaborative editing. |
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
